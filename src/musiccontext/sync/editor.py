"""One ffmpeg pass from video + music + AlignmentPlan to a finished file.

The result reports what actually happened: the filters that really went into the graph and the
output duration measured back off the rendered file, never the duration we hoped for.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ..analysis.media import ffprobe, run, which
from ..errors import CorruptMediaError, InvalidArgumentError, MusicContextError
from ..schemas import AlignmentPlan, DuckingSpec
from ..security.paths import PathPolicy
from ..security.secrets import redact
from . import ducking as duck
from .fades import (
    adelay_filter,
    afade_in,
    afade_out,
    apad_to,
    atrim_seek_args,
    finite,
    format_filter,
    loudnorm_filter,
    num,
    volume_db,
)
from .looping import loop_filter_chain, plan_loop

SR = 48000
CHANNELS = 2
DURATION_TOLERANCE = 0.25
FASTSTART_SUFFIXES = {".mp4", ".m4v", ".mov", ".m4a"}


@dataclass
class RenderPlan:
    video: Path
    music: Path
    output: Path
    alignment: AlignmentPlan
    ducking: DuckingSpec | None = None
    speech: list = field(default_factory=list)
    keep_original_audio: bool = True
    original_gain_db: float = 0.0
    target_lufs: float | None = None
    ducking_mode: str = "expression"
    copy_video: bool = True
    force: bool = False


@dataclass
class RenderResult:
    output: Path
    duration: float
    expected_duration: float
    filters: list[str]
    command: list[str]
    applied: list[str]
    warnings: list[str]
    sync_points: list
    ducking_applied: bool
    audio_streams: int

    def command_display(self) -> str:
        return redact(shlex.join(self.command))


def _float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _duration_from(info: dict) -> float:
    best = _float(info.get("format", {}).get("duration")) or 0.0
    for st in info.get("streams", []):
        best = max(best, _float(st.get("duration")) or 0.0)
    return best


def probe_duration(path: Path, settings) -> float:
    d = _duration_from(ffprobe(Path(path), settings))
    if d <= 0:
        raise CorruptMediaError(
            f"'{Path(path).name}' reports no duration.",
            "Re-mux it (`ffmpeg -i in -c copy out.mp4`) so the container carries a duration, or pass a different file.",
        )
    return d


def _count(info: dict, kind: str) -> int:
    return sum(1 for s in info.get("streams", []) if s.get("codec_type") == kind)


def render(plan: RenderPlan, settings, *, policy: PathPolicy | None = None, dry_run: bool = False) -> RenderResult:
    policy = policy or PathPolicy(None)
    video = policy.input_file(plan.video, "video")
    music = policy.input_file(plan.music, "music")
    out = policy.output_file(plan.output, force=plan.force, protect=(video, music))

    mode = plan.ducking_mode
    if mode not in ("expression", "sidechain"):
        raise InvalidArgumentError(
            f"Unknown ducking_mode {mode!r}.",
            "Use 'expression' (default: time-based, exact, reproducible) or 'sidechain' (level-driven from the dialogue).",
        )

    vinfo = ffprobe(video, settings)
    minfo = ffprobe(music, settings)
    if not _count(vinfo, "video"):
        raise InvalidArgumentError(f"'{video.name}' has no video stream.", "Pass a video file; there is nothing to render onto otherwise.")
    if not _count(minfo, "audio"):
        raise InvalidArgumentError(f"'{music.name}' has no audio stream.", "Pass an audio file (wav/mp3/flac/m4a) as the music.")
    vdur = _duration_from(vinfo)
    mdur = _duration_from(minfo)
    if vdur <= 0 or mdur <= 0:
        raise CorruptMediaError(
            f"Could not read a duration for '{(video if vdur <= 0 else music).name}'.",
            "Re-mux the file (`ffmpeg -i in -c copy out.mp4`) so its duration is known.",
        )
    video_audio = _count(vinfo, "audio")
    subtitles = _count(vinfo, "subtitle")

    a = plan.alignment
    delay = max(0.0, finite("video_delay", a.video_delay))
    mstart = max(0.0, finite("music_start", a.music_start))
    if delay >= vdur:
        raise InvalidArgumentError(
            f"video_delay {delay:.2f}s is not shorter than the video ({vdur:.2f}s), so no music would be heard.",
            "Lower the alignment video_delay.",
        )
    if mstart >= mdur:
        raise InvalidArgumentError(
            f"music_start {mstart:.2f}s is past the end of '{music.name}' ({mdur:.2f}s).",
            "Lower the alignment music_start or pass a longer track.",
        )
    span = vdur - delay  # the music chain works in its own local time; adelay shifts it at the end

    applied: list[str] = []
    warnings: list[str] = []

    # --- music chain -------------------------------------------------------------------
    seek = atrim_seek_args(mstart)
    if seek:
        applied.append(f"seeked {num(mstart)}s into the track (accurate input-side -ss)")
    stmts = [f"[1:a]{format_filter(SR, CHANNELS)}[mfmt]"]

    label = "mfmt"
    if a.loop and (mdur - mstart) < span - 0.05:
        lp = plan_loop(mdur, mstart, span, loop_end=a.loop_end, crossfade=a.loop_crossfade)
        loop_stmts, label = loop_filter_chain(lp, "mfmt", "mloop", sample_rate=SR)
        stmts += loop_stmts
        applied.append(
            f"looped the track {lp.repeats}x to cover {num(span)}s"
            + (f" with a {num(lp.crossfade)}s crossfade" if lp.crossfade > 0 else " (sample-accurate aloop)")
        )
        warnings += lp.notes
    elif (mdur - mstart) < span - 0.05:
        warnings.append(
            f"the track only covers {num(mdur - mstart)}s of the {num(span)}s it has to fill; "
            "the rest is silence (set the alignment loop flag to repeat it)."
        )

    post = [apad_to(span)]
    fi = afade_in(a.fade_in)
    if fi:
        post.append(fi)
        applied.append(f"fade in {num(a.fade_in)}s at {num(delay)}s")
    fo_len = max(0.0, finite("fade_out", a.fade_out))
    if fo_len > 0:
        fo_start = max(0.0, span - fo_len)
        fo = afade_out(fo_len, fo_start)
        if fo:
            post.append(fo)
            applied.append(f"fade out {num(fo_len)}s at {num(fo_start + delay)}s")

    spec = plan.ducking
    blocks: list[tuple[float, float]] = []
    duck_on = False
    if spec is not None and spec.enabled:
        blocks = duck.ducking_blocks(plan.speech)
        unmerged = duck.ducking_blocks(plan.speech, max_blocks=1_000_000)
        if len(blocks) < len(unmerged):
            warnings.append(f"{len(unmerged)} speech blocks merged down to {len(blocks)} to keep the ducking filter string parseable.")
        if not blocks:
            warnings.append("ducking was requested but no speech segments were supplied; the music is left at full level.")
        elif mode == "sidechain" and video_audio == 0:
            warnings.append(f"sidechain ducking needs an audio stream in '{video.name}'; fell back to the time-based expression.")
            mode = "expression"

    gain = finite("gain_db", a.gain_db)
    if blocks:
        gain += finite("bed_gain_db", spec.bed_gain_db)
    vol = volume_db(gain)
    if vol:
        post.append(vol)
        applied.append(f"music gain {num(gain)} dB")

    if blocks and mode == "expression":
        # Blocks are video-timeline times; the chain runs in music-local time, so shift by the delay.
        local = [(b0 - delay, b1 - delay) for b0, b1 in blocks]
        expr = duck.duck_expression(local, spec.duck_depth_db, spec.attack_ms, spec.release_ms, span)
        if expr:
            post.append(expr)
            duck_on = True
            applied += duck.describe_ducking(blocks, spec.duck_depth_db)
        else:
            warnings.append("ducking was requested but the depth is 0 dB, so nothing was attenuated.")

    if plan.target_lufs is not None:
        post.append(loudnorm_filter(plan.target_lufs))
        applied.append(f"loudness normalised to {num(plan.target_lufs)} LUFS (single-pass)")
        if duck_on:
            warnings.append("loudnorm runs after the ducking and is level-driven, so it partly lifts the ducked passages back up.")

    ad = adelay_filter(delay, CHANNELS)
    if ad:
        post.append(ad)
        applied.append(f"music delayed {num(delay)}s onto the video timeline")

    stmts.append(f"[{label}]" + ",".join(post) + "[mpre]")

    # --- dialogue + mix ----------------------------------------------------------------
    keep_orig = plan.keep_original_audio and video_audio > 0
    sidechain = bool(blocks) and mode == "sidechain"
    music_label = "mpre"
    orig_label = "dia"
    if keep_orig or sidechain:
        og = volume_db(plan.original_gain_db) if keep_orig else None
        head = f"[0:a]{format_filter(SR, CHANNELS)}" + (f",{og}" if og else "")
        if keep_orig and sidechain:
            stmts.append(f"{head},asplit=2[dia][dkey]")
        elif keep_orig:
            stmts.append(f"{head}[dia]")
        else:
            stmts.append(f"{head}[dkey]")
        if og:
            applied.append(f"original audio gain {num(plan.original_gain_db)} dB")
    if sidechain:
        sc = duck.sidechain_filters(spec.duck_depth_db, spec.attack_ms, spec.release_ms)
        if sc:
            stmts.append(f"[mpre][dkey]{sc[0]}[mduck]")
            music_label = "mduck"
            duck_on = True
            applied.append(f"music sidechain-ducked against the dialogue (~{finite('depth', spec.duck_depth_db):.1f} dB target, level-driven)")

    if keep_orig:
        # normalize=0: amix must not attenuate the dialogue just because a second input arrived.
        stmts.append(f"[{orig_label}][{music_label}]amix=inputs=2:normalize=0:duration=longest[aout]")
        aout = "aout"
        applied.append("original audio kept at full level (amix normalize=0)")
    else:
        aout = music_label
        if video_audio:
            applied.append("original audio dropped (keep_original_audio=False)")
        else:
            applied.append(f"'{video.name}' has no audio stream; the output carries the music only")

    # --- command ------------------------------------------------------------------------
    exe = which("ffmpeg", settings)
    tmp = out.with_name(f"{out.stem}.part-{os.getpid()}{out.suffix}")
    argv = [exe, "-y", "-v", "error", "-nostdin", "-i", str(video), *seek, "-i", str(music),
            "-filter_complex", ";".join(stmts), "-map", "0:v", "-map", f"[{aout}]"]
    if subtitles:
        argv += ["-map", "0:s?", "-c:s", "copy"]
        applied.append(f"{subtitles} subtitle stream(s) copied through")
    argv += ["-c:v", "copy"] if plan.copy_video else ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
    argv += ["-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", str(CHANNELS)]
    # apad already stretched the music to the video length, so the video is the shortest stream
    # and -shortest lands the output exactly on the video duration instead of on the music's.
    argv += ["-shortest"]
    if out.suffix.lower() in FASTSTART_SUFFIXES:
        argv += ["-movflags", "+faststart"]
    argv += [str(tmp)]

    if dry_run:
        return RenderResult(
            output=out, duration=0.0, expected_duration=vdur, filters=stmts, command=argv,
            applied=[*applied, "dry run: nothing was decoded, rendered or written"], warnings=warnings,
            sync_points=list(a.sync_points), ducking_applied=duck_on, audio_streams=0,
        )

    try:
        cp = run(argv, timeout=3600, check=False)
    except MusicContextError:
        tmp.unlink(missing_ok=True)
        raise
    if cp.returncode != 0 or not tmp.exists():
        tail = (cp.stderr or "").strip().splitlines()
        tmp.unlink(missing_ok=True)
        raise CorruptMediaError(
            f"ffmpeg could not render '{out.name}': {redact(tail[-1]) if tail else 'no error output'}",
            "Check that the music decodes (`ffprobe <music>`) and that the output container accepts AAC audio.",
        )
    os.replace(tmp, out)

    oinfo = ffprobe(out, settings)
    odur = _duration_from(oinfo)
    if abs(odur - vdur) > DURATION_TOLERANCE:
        warnings.append(f"output is {odur:.2f}s but the video is {vdur:.2f}s ({odur - vdur:+.2f}s); the mux trimmed or padded the result.")
    return RenderResult(
        output=out, duration=odur, expected_duration=vdur, filters=stmts, command=argv,
        applied=applied, warnings=warnings, sync_points=list(a.sync_points),
        ducking_applied=duck_on, audio_streams=_count(oinfo, "audio"),
    )
