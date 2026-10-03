"""Music Direction Engine: AnalysisReport + preferences (+ brand profile) -> MusicDirection with explanation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..schemas import (
    AnalysisReport,
    BrandProfile,
    Explanation,
    MusicDirection,
    MusicMarker,
    Preferences,
)
from ..timeline.events import build_markers, build_timeline, voiceover_blocks
from . import presets
from .arc import build_arc
from .constraints import build_constraints, ducking_for
from .energy import music_curve_from_story, rhythm_for
from .instrumentation import choose_instrumentation
from .mood import STYLES, Features, rank_moods, rank_styles
from .tempo import choose_tempo

PACE_NUM = {"slow": 0.1, "moderate": 0.5, "fast": 0.9}


@dataclass
class Plan:
    directions: list[MusicDirection]
    markers: list[MusicMarker]
    timeline: list[dict]
    sync_opportunities: list[dict]


def _norm(xs) -> list[str]:
    return [str(x).strip().lower().replace(" ", "_") for x in xs if str(x).strip()]


def _features(rep: AnalysisReport) -> Features:
    d = rep.metadata.duration
    sc = rep.scenes
    w = np.array([s.duration for s in sc]) / max(sum(s.duration for s in sc), 1e-6)

    def avg(attr):
        return float(sum(getattr(s, attr) * wi for s, wi in zip(sc, w, strict=True)))

    curve = rep.story.energy_curve
    return Features(
        energy=float(np.mean([p[1] for p in curve])) if curve else avg("energy"),
        pace=float(sum(PACE_NUM[s.pace] * wi for s, wi in zip(sc, w, strict=True))),
        brightness=avg("brightness"), saturation=avg("saturation"), warmth=avg("warmth"),
        speech_ratio=float(rep.stats.get("speech_ratio") or 0.0) if d else 0.0,
    )


def create_plan(rep: AnalysisReport, prefs: Preferences | None = None, profile: BrandProfile | None = None, variants: int = 1) -> Plan:
    prefs = prefs or Preferences()
    markers = build_markers(rep, prefs)
    f = _features(rep)
    preset_name = prefs.platform or prefs.content_type
    preset = presets.get(preset_name)
    cut_times = [t.start_time for t in rep.transitions]
    directions = []
    user_styles = _norm([*prefs.style, *prefs.genre])
    profile_styles = _norm(profile.preferred_styles) if profile else []
    avoid = _norm([*prefs.avoid, *(profile.avoid if profile else []), *preset.get("avoid", [])])
    ranked = rank_styles(f, _norm(preset.get("style", [])) + profile_styles, avoid)
    for v in range(max(1, variants)):
        if v == 0 and user_styles:
            styles, style_ev = user_styles[:2], ["styles specified by the user"]
        elif v == 0:
            top = ranked[0]
            styles, style_ev = [top[0]], top[2]
            if len(ranked) > 1 and ranked[1][1] >= top[1] - 0.06:
                styles.append(ranked[1][0])
                style_ev = style_ev + [f"{ranked[1][0]} scores within 0.06 so it is blended in"]
        else:
            pool = [r for r in ranked if r[0] not in directions[0].style]
            pick = pool[min(v - 1, len(pool) - 1)] if pool else ranked[min(v, len(ranked) - 1)]
            styles, style_ev = [pick[0]], pick[2] + [f"alternative direction #{v}: next-best style not used by the primary"]
        directions.append(_direction(rep, prefs, profile, f, markers, preset, preset_name, ranked, styles, style_ev, cut_times, avoid,
                                     label="primary" if v == 0 else f"alternative-{v}", energy_shift=-0.1 * (v % 2) if v else 0.0))
    tl = build_timeline(markers, rep, directions[0].arc.sections)
    return Plan(directions, markers, tl, _opportunities(markers, directions[0], rep, cut_times))


def _direction(rep, prefs, profile, f, markers, preset, preset_name, ranked, styles, style_ev, cut_times, avoid, label, energy_shift) -> MusicDirection:
    d = rep.metadata.duration
    speech_ratio = f.speech_ratio
    has_speech = bool(rep.speech) and speech_ratio > 0.02
    # moods
    mood_user = _norm([*prefs.mood, *prefs.brand_personality, *(profile.mood if profile else []), *preset.get("mood", [])])
    mood_rank = rank_moods(f, avoid)
    moods = list(dict.fromkeys(mood_user[:3] + [m for m, _ in mood_rank[:2]]))[:3]
    mood_ev = f"moods: {', '.join(moods)} ({'user/profile/preset-specified + ' if mood_user else ''}derived from energy {f.energy:.2f}, brightness {f.brightness:.2f})"
    # energy
    curve = music_curve_from_story(rep.story.energy_curve, preset.get("energy_bias", 0.0) + energy_shift, prefs.energy)
    energy = float(np.mean([p[1] for p in curve])) if curve else f.energy
    # tempo
    pr = tuple(preset["bpm"]) if "bpm" in preset else None
    tempo, tempo_ev = choose_tempo(energy, rep.story.pace, speech_ratio, cut_times, prefs.bpm, pr)  # type: ignore[arg-type]
    # vocals
    vocals = prefs.vocals or (profile.vocals if profile else None) or preset.get("vocals") or "none"
    if has_speech and speech_ratio >= 0.15 and vocals in ("preferred", "allowed") and not prefs.vocals:
        vocals = "none"
    # arc
    arc = build_arc(rep.story, curve, markers, rep.speech, preset_name)
    # instrumentation / texture
    inst = choose_instrumentation(styles, _norm(prefs.instruments), _norm(profile.preferred_instrumentation) if profile else [], avoid, speech_ratio)
    texture = list(dict.fromkeys(t for s in styles for t in STYLES.get(s, {}).get("tex", [])))[:3] or ["hybrid"]
    if has_speech and "spacious" not in texture:
        texture.append("spacious")
    rhythm = rhythm_for(energy, speech_ratio > 0.4)
    ducking = ducking_for(speech_ratio, has_speech, tempo.target_bpm)
    constraints, req = build_constraints(prefs, vocals, (tempo.min_bpm, tempo.max_bpm), speech_ratio, d, inst.prohibited, preset_name, _norm(profile.avoid) if profile else [])
    if prefs.duration:
        req = req.model_copy(update={"min_duration": prefs.duration * 0.6})
    # explanation
    ev = [
        f"visual pacing is {rep.story.pace} (median scene {rep.stats['median_scene_length_s']}s, {rep.stats['cuts_per_minute']} cuts/min)",
        f"visual look: {', '.join(rep.story.visual_style[:5])}",
        *style_ev[:3], mood_ev, *tempo_ev[:3],
    ]
    if has_speech:
        ev.append(f"voiceover occupies {speech_ratio:.0%} of the timeline ({rep.stats['speech_source']}); {'dense' if speech_ratio > 0.6 else 'moderate'} instrumentation would compete with narration")
    if arc.peak_time is not None:
        src = "user marker" if any(m.source == "user" and abs(m.start_time - arc.peak_time) < 1.5 for m in markers) else "inferred from the energy curve"
        pk = next((s for s in arc.sections if s.label == "peak"), None)
        ev.append(f"peak/reveal at {arc.peak_time:.1f}s ({src}); target energy rises to {pk.energy:.2f} there" if pk else f"peak/reveal at {arc.peak_time:.1f}s ({src})")
    when = [f"{s.start_time:.1f}-{s.end_time:.1f}s {s.label}: energy {s.energy:.2f}, {s.rhythm} rhythm, action={s.action}" for s in arc.sections]
    for a, b in voiceover_blocks(rep.speech)[:6]:
        when.append(f"{a:.1f}-{b:.1f}s voiceover: duck music by {ducking.duck_depth_db:.0f} dB")
    conf = 0.45
    notes = ["heuristic analysis only (no vision model): scene roles and the reveal are inferences"]
    if any(m.source == "user" for m in markers):
        conf += 0.15
        notes.append("user markers anchor key moments")
    if rep.stats["speech_source"] in ("transcript", "embedded_subtitles"):
        conf += 0.1
        notes.append("speech timing comes from a transcript/subtitles")
    elif has_speech:
        notes.append("speech timing is a heuristic estimate")
    if d < 6:
        conf -= 0.1
        notes.append("very short video: less evidence")
    if prefs.style or prefs.mood:
        conf += 0.05
    what = f"{' + '.join(styles)} instrumental direction, {', '.join(moods)}, ~{tempo.target_bpm:.0f} BPM ({tempo.min_bpm:.0f}-{tempo.max_bpm:.0f}), {rhythm} rhythm" if vocals == "none" else \
           f"{' + '.join(styles)} direction with vocals {vocals}, {', '.join(moods)}, ~{tempo.target_bpm:.0f} BPM"
    why = [e for e in ev[:2]] + ([f"voiceover present, so rhythm stays {rhythm} and vocals are excluded"] if has_speech else []) + \
          ([f"build leads into the peak at {arc.peak_time:.1f}s"] if arc.peak_time is not None else [])
    expl = Explanation(what=what, why=why, when=when, evidence=ev, confidence=round(min(max(conf, 0.1), 0.85), 2), confidence_note="; ".join(notes))
    sections_txt = "; ".join(f"{s.start_time:.0f}-{s.end_time:.0f}s {s.label} (energy {s.energy:.2f})" for s in arc.sections)
    brief = (f"{' / '.join(styles).capitalize()} soundtrack, {', '.join(moods)}. Target {tempo.target_bpm:.0f} BPM ({tempo.min_bpm:.0f}-{tempo.max_bpm:.0f}), {rhythm} rhythm, "
             f"{'/'.join(texture)} texture. {'Instrumental. ' if vocals == 'none' else ''}Preferred instruments: {', '.join(inst.preferred) or 'open'}"
             f"{'; avoid ' + ', '.join(inst.prohibited) if inst.prohibited else ''}. Arc: {sections_txt}. "
             f"{'Land the main hit exactly at ' + format(arc.peak_time, '.1f') + 's. ' if arc.peak_time is not None else ''}"
             f"{'Leave 1-4 kHz open for narration. ' if has_speech else ''}Total length {d:.1f}s, ending: {arc.ending}.")
    gprompt = (f"{', '.join(styles)} instrumental, {', '.join(moods)}, {tempo.target_bpm:.0f} BPM, {', '.join(texture)}, "
               f"{', '.join(inst.preferred[:5])}; starts {arc.sections[0].rhythm if arc.sections else 'sparse'}, builds to a peak"
               f"{' at ' + format(arc.peak_time, '.0f') + ' seconds' if arc.peak_time is not None else ''}, resolves cleanly") + ("; no vocals" if vocals == "none" else "")
    return MusicDirection(
        label=label, style=styles, mood=moods, energy=round(energy, 3), tempo=tempo, rhythm=rhythm, instrumentation=inst, texture=texture,  # type: ignore[arg-type]
        vocals=vocals, arc=arc, ducking=ducking, constraints=constraints, requirement=req, explanation=expl, brief=brief, generation_prompt=gprompt[:1900],
    )


def _opportunities(markers, direction, rep, cut_times) -> list[dict]:
    out = []
    for m in markers:
        if m.type in ("hard_cut", "scene_change") and m.importance < 0.6:
            continue
        if m.type == "hard_cut":
            continue
        how = {
            "peak": "Land the biggest musical hit (downbeat + new layer) exactly here.",
            "lift": "Introduce a new layer or lift on the downbeat nearest this moment.",
            "hit": "Place an accent (hit/riser end) on or just before this moment.",
            "resolve": "Resolve the phrase here; avoid starting new material.",
            "duck": "Start lowering the music ~0.2s before this moment.",
            "release": "Bring the music back up over ~0.5s after this moment.",
            "transition_fill": "Use a short fill/riser or change of texture to mask the transition.",
            "release_": "",
        }.get(m.suggested_music_action, "")
        if how:
            out.append({"time": m.start_time, "marker": m.type, "action": m.suggested_music_action, "importance": m.importance, "how": how, "basis": m.source})
    if cut_times:
        from ..timeline.transitions import cut_rhythm

        rh = cut_rhythm(cut_times)
        if rh["regular"]:
            out.append({"time": None, "marker": "cut_rhythm", "action": "tempo_match", "importance": 0.7,
                        "how": f"Cuts recur about every {rh['interval']:.2f}s; a tempo of {direction.tempo.target_bpm:.1f} BPM places them on beats.", "basis": "estimated"})
    out.sort(key=lambda r: (-(r["importance"]), r["time"] or 0))
    return out[:40]
