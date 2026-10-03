"""Video analysis pipeline: probe -> (cuts || audio) -> frames -> speech -> story, with caching and timings."""

from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..analysis import speech as speech_mod
from ..analysis.audio import analyze_series, runs
from ..analysis.media import content_hash
from ..analysis.scenes import cuts_to_transitions, detect_cuts, sample_frames
from ..analysis.story import build_report
from ..analysis.video import inspect_video
from ..schemas import AnalysisReport, SpeechSegment
from .cache import Cache, make_key
from .context import Settings

log = logging.getLogger("musiccontext.pipeline")


def _cache(settings: Settings) -> Cache:
    return Cache(settings.cache_dir, enabled=not settings.no_cache)


def analyze_video(path: Path, settings: Settings, *, transcript: Path | None = None, cut_threshold: float = 0.28) -> tuple[AnalysisReport, bool]:
    """Returns (report, cache_hit)."""
    t0 = time.perf_counter()
    meta = inspect_video(path, settings, with_hash=False)
    h = content_hash(path)
    meta = meta.model_copy(update={"sha256": h})
    tx_id = hashlib.sha256(transcript.read_bytes()).hexdigest() if transcript else None
    cache = _cache(settings)
    key, material = make_key("analysis", h, {"cut_threshold": cut_threshold}, {"transcript": tx_id})
    if (hit := cache.get(key, material)) is not None:
        rep = AnalysisReport.model_validate(hit)
        return rep.model_copy(update={"metadata": meta}), True  # path may differ from when it was cached
    timings = {"probe": round(time.perf_counter() - t0, 3)}
    warnings: list[str] = []
    caps = {
        "scene_detection": "ffmpeg scene score (detected)",
        "motion_and_colour": "64x36 frame differencing and colour statistics (estimated)",
        "ocr": "not performed - supply user markers for titles/logos/product moments",
        "object_detection": "not performed",
        "vision_model": "not used (deterministic heuristics only)",
    }

    def timed(name, fn):
        s = time.perf_counter()
        r = fn()
        timings[name] = round(time.perf_counter() - s, 3)
        return r

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_cuts = ex.submit(timed, "scene_detection", lambda: detect_cuts(path, meta.duration, settings, cut_threshold))
        f_aud = ex.submit(timed, "audio", lambda: analyze_series(path, settings)) if meta.has_audio else None
        cuts = f_cuts.result()
        audio = f_aud.result() if f_aud else None
    fs = timed("frames", lambda: sample_frames(path, meta.duration, [c.time for c in cuts], settings))

    speech: list[SpeechSegment] = []
    speech_source = "none"
    if transcript is not None:
        speech, speech_source = speech_mod.load_transcript(transcript), "transcript"
        caps["speech"] = f"timings from {transcript.name} (detected); text discarded, only timing/word counts kept"
    elif meta.subtitle_streams:
        speech = timed("subtitles", lambda: speech_mod.embedded_subtitles(path, settings))
        if speech:
            speech_source = "embedded_subtitles"
            caps["speech"] = "embedded subtitle stream timings (detected)"
    if not speech and audio is not None and not audio.empty():
        speech, diag = timed("speech", lambda: speech_mod.detect_speech(audio))
        speech_source = "heuristic_vad" if speech or diag else "none"
        caps["speech"] = "heuristic voice-activity detection (estimated, confidence <= 0.6); pass --transcript for exact timing"
        warnings.append("Speech intervals are heuristic estimates; provide --transcript/subtitles for exact voiceover timing.") if speech else None
    elif not meta.has_audio:
        caps["speech"] = "unavailable: video has no audio stream"

    existing = None
    if audio is not None and not audio.empty() and meta.duration <= 900:
        sp = np.zeros(len(audio.rms_db), bool)
        for s in speech:
            sp[int(s.start_time * audio.fps) : int(s.end_time * audio.fps) + 1] = True
        act = (audio.rms_db > -45) & ~sp
        if act.mean() >= 0.25 and sum(b - a for a, b in runs(act, audio.fps, 4.0, 0.5)) >= 8.0:
            from ..music.features import analyze_music

            try:
                f = timed("existing_music", lambda: analyze_music(path, settings, loudness=False, max_seconds=300))
                if f.bpm and f.bpm.confidence >= 0.6:
                    existing = {"bpm": f.bpm.value, "confidence": round(f.bpm.confidence * 0.7, 2)}
            except Exception as e:  # noqa: BLE001 - optional enrichment must never fail analysis
                log.debug("existing-music estimate skipped: %s", e)
    rep = build_report(meta, cuts, cuts_to_transitions(cuts), fs, audio, speech, speech_source, caps, warnings, timings, existing)
    rep.timings_s["total"] = round(time.perf_counter() - t0, 3)
    cache.put(key, material, rep.model_dump(mode="json"))
    return rep, False
