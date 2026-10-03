"""How a track sits against the video: seek, delay, loop, fades, level, and the sync points that result.

Every decision lands in `notes` with the reasoning that produced it, because an editor has to be able
to disagree with one choice (a 0.5 s delay, a loop point) without re-deriving the whole placement.
Nothing here invents measurements: with no beat data the plan says so and `basis` becomes "none".
"""

from __future__ import annotations

from ..errors import InvalidArgumentError
from ..schemas import AlignmentPlan, AnalysisReport, MusicCandidate, MusicDirection, MusicMarker
from ..timeline.events import voiceover_blocks
from ..timeline.sync import find_sync_points, music_events

VO_OPENING = 1.5  # narration starting inside this window counts as "the video opens on a word"
MAX_VIDEO_DELAY = 0.5
NATURAL_TOLERANCE = 0.4  # how close the track's own end must land to the video end to need no fade
LOOP_CROSSFADE = 1.0  # used only when there is no downbeat to loop on; a bar line needs no crossfade
BED_LUFS_TARGET = -22.0  # a music bed under speech normally sits around -22 LUFS integrated
GAIN_LIMITS = (-30.0, 6.0)
# SyncPoint.quality / AlignmentPlan.basis only have three honest values. A provider's beat claim is
# not something we measured, so it is reported as "estimated" rather than "detected".
_BASIS = {"detected": "detected", "user": "detected", "inferred": "inferred", "provider": "estimated", "estimated": "estimated"}


def plan_alignment(candidate: MusicCandidate, direction: MusicDirection, analysis: AnalysisReport,
                   markers: list[MusicMarker], *, prefer_natural_ending: bool = True) -> AlignmentPlan:
    f = candidate.features
    if f is None:
        raise InvalidArgumentError(
            f"Candidate '{candidate.id}' has no analyzed features, so it cannot be placed against the video.",
            "Run musiccontext.music.features.analyze_music() on the audio first, or pick a provider that "
            "returns duration/beats, then call plan_alignment again.",
        )
    video = analysis.metadata.duration
    sections = direction.arc.sections
    has_beats = bool(f.beats or f.downbeats)
    notes: list[str] = []

    # --- video_delay: only to keep the music off the first spoken word of an intro
    video_delay = 0.0
    blocks = voiceover_blocks(analysis.speech)
    first_is_intro = bool(sections) and sections[0].label == "intro"
    if blocks and blocks[0][0] <= VO_OPENING and first_is_intro:
        video_delay = MAX_VIDEO_DELAY
        notes.append(f"narration starts at {blocks[0][0]:.2f}s and the first arc section is an intro: music enters "
                     f"{video_delay:.2f}s late so it does not step on the first word")
    elif blocks and blocks[0][0] <= VO_OPENING:
        notes.append(f"narration starts at {blocks[0][0]:.2f}s but the first arc section is "
                     f"'{sections[0].label if sections else 'none'}', not an intro: music starts with the video")

    # --- music_start: put the strongest reachable lift on the peak marker
    peak = direction.arc.peak_time
    max_start = max(0.0, f.duration - (video - video_delay))
    music_start = 0.0
    if peak is None or not f.lifts:
        notes.append("music_start 0.00s: " + ("the direction has no peak moment to hit" if peak is None
                                              else "no energy lifts were detected in the track to place on the peak"))
    else:
        placed = []
        for t, strength in f.lifts:
            start = min(max(t - peak + video_delay, 0.0), max_start)
            residual = abs((t - start + video_delay) - peak)
            placed.append((residual, -strength, t, start))
        best = min(placed, key=lambda p: (round(p[0], 3), p[1], p[2]))
        music_start = round(best[3], 3)
        notes.append(f"music_start {music_start:.2f}s places the lift at track {best[2]:.2f}s (strength {-best[1]:.2f}) "
                     f"on the {peak:.1f}s peak with {best[0] * 1000:.0f} ms residual error"
                     + (f"; the seek is capped at {max_start:.2f}s so the track still reaches the end of the video"
                        if max_start <= music_start + 1e-6 and best[0] > 1e-6 else ""))

    # --- loop: only when what is left of the track cannot reach the end of the video
    reach = video_delay + (f.duration - music_start)
    loop = reach < video - 1e-3
    loop_end: float | None = None
    loop_crossfade = 0.0
    if loop:
        tail = [d for d in f.downbeats if d > music_start]
        if tail:
            loop_end = round(tail[-1], 3)
            notes.append(f"looping from the last downbeat at {loop_end:.2f}s: {f.duration:.1f}s of track covers "
                         f"{reach:.1f}s of a {video:.1f}s video, and a bar line needs no crossfade")
        else:
            loop_end = round(f.duration, 3)
            loop_crossfade = LOOP_CROSSFADE
            notes.append(f"looping from the track end at {loop_end:.2f}s with a {loop_crossfade:.1f}s crossfade: "
                         f"no downbeats were found, so the seam is not musical and has to be hidden")
    else:
        notes.append(f"no loop needed: {f.duration - music_start:.1f}s of track covers the {video:.1f}s video")

    # --- fades
    fade_in = 2.0 if first_is_intro else 0.5
    notes.append(f"fade_in {fade_in:.1f}s: " + ("the first arc section is an intro, so a slow entry is in character"
                                               if first_is_intro else "a short entry avoids a hard start without delaying the music"))
    track_end_on_video = video_delay + (f.duration - music_start)
    exact = not loop and abs(track_end_on_video - video) <= NATURAL_TOLERANCE
    natural_ending = bool(prefer_natural_ending and exact)
    ending = direction.arc.ending
    if ending == "fade_out":
        fade_out = 2.0
    elif ending in ("resolve", "hard_stop"):
        fade_out = 0.3 if natural_ending else 1.5
    else:
        fade_out = 1.5
    if natural_ending:
        notes.append(f"the track's own end lands at {track_end_on_video:.2f}s, within {NATURAL_TOLERANCE:.1f}s of the "
                     f"{video:.1f}s video end: keep the natural ending and fade only {fade_out:.1f}s")
    else:
        why = ("the plan loops, so there is no natural end" if loop else
               f"the track's end would land at {track_end_on_video:.2f}s against a {video:.1f}s video"
               if not exact else "prefer_natural_ending is off")
        notes.append(f"fade_out {fade_out:.1f}s (arc ending '{ending}'): {why}")

    # --- gain
    gain_db = 0.0
    if analysis.speech:
        gain_db = direction.ducking.bed_gain_db
        if f.loudness_lufs is None:
            notes.append(f"gain {gain_db:+.1f} dB from the direction's bed level; the track's loudness is unverified "
                         f"(never measured), so no normalisation was applied")
        else:
            trim = BED_LUFS_TARGET - f.loudness_lufs
            gain_db = round(min(max(gain_db + trim, GAIN_LIMITS[0]), GAIN_LIMITS[1]), 2)
            notes.append(f"gain {gain_db:+.1f} dB: measured {f.loudness_lufs:.1f} LUFS ({f.loudness_source}) normalised "
                         f"toward a {BED_LUFS_TARGET:.0f} LUFS music bed, plus the direction's "
                         f"{direction.ducking.bed_gain_db:+.1f} dB bed offset")
    else:
        notes.append("gain 0.0 dB: the video has no speech, so the music does not have to make room for narration")

    # --- sync points
    quality = _BASIS.get(f.beats_source, "estimated")
    points = find_sync_points(markers, music_events(f, placement_offset=video_delay - music_start), quality=quality,
                              min_importance=0.5) if has_beats else []
    if has_beats:
        notes.append(f"{len(points)} of the {sum(1 for m in markers if m.importance >= 0.5)} important markers land "
                     f"within 150 ms of a musical event ({quality} beat data)")
    else:
        notes.append("no beat data: no sync points could be computed, and every timing here is derived from the "
                     "container duration alone")

    return AlignmentPlan(
        music_start=music_start, video_delay=round(video_delay, 3), loop=loop, loop_end=loop_end,
        loop_crossfade=loop_crossfade, fade_in=fade_in, fade_out=fade_out, natural_ending=natural_ending,
        gain_db=gain_db, sync_points=points, notes=notes,
        basis=quality if has_beats else "none",  # type: ignore[arg-type]
    )


def alignment_summary(plan: AlignmentPlan) -> str:
    bits = [f"Start the track {plan.music_start:.2f}s in" if plan.music_start else "Start the track from its beginning"]
    if plan.video_delay:
        bits.append(f"{plan.video_delay:.2f}s after the video starts")
    if plan.loop and plan.loop_end is not None:
        seam = f" with a {plan.loop_crossfade:.1f}s crossfade" if plan.loop_crossfade else " on the bar line"
        bits.append(f"loop from {plan.loop_end:.2f}s{seam}")
    else:
        bits.append("play it through once")
    bits.append(f"fade in {plan.fade_in:.1f}s")
    bits.append("let it end naturally" if plan.natural_ending else f"fade out {plan.fade_out:.1f}s")
    if plan.gain_db:
        bits.append(f"set the bed {plan.gain_db:+.1f} dB")
    tail = (f" {len(plan.sync_points)} marker(s) land on a musical event ({plan.basis} beat data)."
            if plan.sync_points else f" No marker lands on a musical event ({plan.basis} beat data).")
    return ", ".join(bits) + "." + tail


def verify_coverage(plan: AlignmentPlan, track_duration: float, video_duration: float) -> list[str]:
    """Warnings for a plan that would leave silence on the timeline or run past the video."""
    out: list[str] = []
    if plan.music_start >= track_duration:
        return [f"music_start {plan.music_start:.2f}s is at or past the {track_duration:.2f}s track end: "
                f"the plan would produce silence. Re-run plan_alignment with the track's real duration."]
    reach = plan.video_delay + (track_duration - plan.music_start)
    if plan.loop:
        if plan.loop_end is not None and plan.loop_end <= plan.music_start:
            out.append(f"loop_end {plan.loop_end:.2f}s is not after music_start {plan.music_start:.2f}s: "
                       f"the loop region is empty. Pick a downbeat later in the track.")
    elif reach < video_duration - 0.05:
        out.append(f"the track runs out at {reach:.2f}s but the video is {video_duration:.2f}s long, leaving "
                   f"{video_duration - reach:.2f}s of silence. Set loop=True or choose a longer track.")
    if plan.video_delay > 0 and plan.video_delay >= video_duration:
        out.append(f"video_delay {plan.video_delay:.2f}s is longer than the {video_duration:.2f}s video: "
                   f"the music would never be heard.")
    if not plan.loop and reach > video_duration + NATURAL_TOLERANCE and plan.fade_out <= 0:
        out.append(f"the track would still be playing {reach - video_duration:.2f}s past the video end with no "
                   f"fade_out: the music will be cut off mid-phrase. Set fade_out or trim with music_start.")
    return out
