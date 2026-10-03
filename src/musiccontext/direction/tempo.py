from __future__ import annotations

from ..schemas import TempoSpec
from ..timeline.transitions import bpm_for_interval, cut_rhythm


def choose_tempo(energy: float, pace: str, speech_ratio: float, cut_times: list[float], user_bpm: float | None,
                 preset_range: tuple[float, float] | None) -> tuple[TempoSpec, list[str]]:
    ev: list[str] = []
    if user_bpm:
        return TempoSpec(target_bpm=user_bpm, min_bpm=round(user_bpm * 0.92, 1), max_bpm=round(user_bpm * 1.08, 1), basis="user",
                         rationale="BPM specified by the user"), ["BPM specified by the user"]
    target = 70 + 75 * energy
    ev.append(f"base tempo {target:.0f} BPM from target energy {energy:.2f}")
    if pace == "fast":
        target += 8
        ev.append("fast visual pacing: +8 BPM")
    elif pace == "slow":
        target -= 8
        ev.append("slow visual pacing: -8 BPM")
    hi_cap = 180.0
    if speech_ratio > 0.4:
        hi_cap = 112.0
        target = min(target, 108.0)
        ev.append(f"voiceover covers {speech_ratio:.0%}: tempo capped near 108 BPM so rhythm does not fight speech")
    lo, hi = 60.0, hi_cap
    if preset_range:
        lo, hi = max(lo, preset_range[0]), min(hi, preset_range[1])
        if lo > hi:
            lo, hi = preset_range
        target = min(max(target, lo), hi)
        ev.append(f"platform preset range {preset_range[0]:.0f}-{preset_range[1]:.0f} BPM applied")
    rh = cut_rhythm(cut_times)
    if rh["regular"] and rh["count"] >= 4:
        cand = bpm_for_interval(rh["interval"], max(lo, target * 0.8), min(hi, target * 1.2), target)
        if cand:
            ev.append(f"cuts recur every {rh['interval']:.2f}s; {cand:.1f} BPM puts every cut on a beat")
            target = cand
    target = round(min(max(target, lo), hi), 1)
    return TempoSpec(target_bpm=target, min_bpm=round(max(55.0, target * 0.88), 1), max_bpm=round(min(190.0, target * 1.12), 1),
                     basis="inferred", rationale="; ".join(ev)), ev
