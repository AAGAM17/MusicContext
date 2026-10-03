"""Speech/voiceover intervals.

Preferred (detected): parsed subtitles/transcript. Fallback (estimated): a transparent
voice-activity heuristic combining voice-band energy share, syllable-rate (2.5-7 Hz)
envelope modulation and natural pauses. The heuristic is NOT a speech recognizer; it
will mislabel some content, so its confidence is capped at 0.6.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from ..schemas import SpeechSegment
from .audio import AudioSeries, runs, smooth
from .media import run, which

_TS = re.compile(r"(\d+):(\d\d):(\d\d)[.,](\d{1,3})|(\d\d):(\d\d)[.,](\d{1,3})")
_TIME_LINE = re.compile(r"(\S+)\s+-->\s+(\S+)")
_TAG = re.compile(r"<[^>]+>|\{[^}]*\}")
MAX_SEGMENTS = 5000


def _ts(s: str) -> float | None:
    m = _TS.fullmatch(s.strip())
    if not m:
        return None
    if m.group(1) is not None:
        h, mi, se, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    else:
        h, mi, se, ms = 0, int(m.group(5)), int(m.group(6)), m.group(7)
    return h * 3600 + mi * 60 + se + int(ms.ljust(3, "0")) / 1000


def parse_subtitles(text: str, provenance: str) -> list[SpeechSegment]:
    """SRT/VTT -> timed segments. Only timing and word COUNT are kept; the text is discarded."""
    segs: list[SpeechSegment] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines) and len(segs) < MAX_SEGMENTS:
        m = _TIME_LINE.search(lines[i])
        if m and (a := _ts(m.group(1))) is not None and (b := _ts(m.group(2))) is not None and b > a:
            i += 1
            words = 0
            while i < len(lines) and lines[i].strip():
                words += len(_TAG.sub(" ", lines[i]).split())
                i += 1
            segs.append(SpeechSegment(start_time=a, end_time=b, confidence=0.9, source="detected", words=words,
                                      density=round(words / (b - a), 2), provenance=provenance))
        else:
            i += 1
    return segs


def parse_transcript_json(text: str, provenance: str) -> list[SpeechSegment]:
    data = json.loads(text)
    items: Any = data.get("segments", data.get("chunks", [])) if isinstance(data, dict) else data
    segs = []
    for it in items[:MAX_SEGMENTS]:
        if not isinstance(it, dict):
            continue
        a = it.get("start", it.get("start_time", it.get("start_s")))
        b = it.get("end", it.get("end_time", it.get("end_s")))
        if a is None or b is None:
            continue
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if b > a >= 0:
            words = len(str(it.get("text", "")).split()) or None
            segs.append(SpeechSegment(start_time=a, end_time=b, confidence=0.9, source="detected", words=words,
                                      density=round(words / (b - a), 2) if words else None, provenance=provenance))
    return segs


def load_transcript(path: Path) -> list[SpeechSegment]:
    text = path.read_text(errors="replace")[:20_000_000]
    prov = f"transcript file {path.name}"
    if path.suffix.lower() == ".json":
        return merge_segments(parse_transcript_json(text, prov))
    return merge_segments(parse_subtitles(text, prov))


def embedded_subtitles(path: Path, settings=None) -> list[SpeechSegment]:
    cp = run([which("ffmpeg", settings), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:s:0", "-f", "srt", "-"], timeout=120, check=False)
    return merge_segments(parse_subtitles(cp.stdout, "embedded subtitle stream 0")) if cp.returncode == 0 else []


def merge_segments(segs: list[SpeechSegment], gap: float = 0.25) -> list[SpeechSegment]:
    segs = sorted(segs, key=lambda s: s.start_time)
    out: list[SpeechSegment] = []
    for s in segs:
        if out and s.start_time - out[-1].end_time <= gap:
            p = out[-1]
            w = (p.words or 0) + (s.words or 0) if (p.words is not None or s.words is not None) else None
            end = max(p.end_time, s.end_time)
            out[-1] = p.model_copy(update={"end_time": end, "words": w, "density": round(w / (end - p.start_time), 2) if w else None})
        else:
            out.append(s)
    return out


def detect_speech(series: AudioSeries) -> tuple[list[SpeechSegment], dict]:
    """Heuristic VAD over a 50 Hz AudioSeries. Returns (segments, diagnostics)."""
    fps = series.fps
    n = len(series.rms_db)
    if n < fps * 2:
        return [], {"reason": "audio shorter than 2s"}
    db = series.rms_db
    peak = float(np.percentile(db, 95))
    if peak < -55:
        return [], {"reason": "audio is effectively silent", "peak_db": round(peak, 1)}
    floor = float(np.percentile(db, 10))
    active = db > max(floor + 12, peak - 35)
    w = int(fps)  # 1 s windows, 0.5 s step
    step = w // 2
    env = db - smooth(db, int(fps * 2))  # remove slow loudness drift
    voice = smooth(series.voice_ratio * active, w) / np.maximum(smooth(active.astype(float), w), 1e-3)
    pause = 1.0 - smooth(active.astype(float), w)
    freqs = np.fft.rfftfreq(w, 1 / fps)
    syl = (freqs >= 2.5) & (freqs <= 7.0)
    allb = (freqs >= 0.5) & (freqs <= 15)
    centers, scores = [], []
    for i in range(0, n - w + 1, step):
        seg = env[i : i + w]
        if seg.std() < 2.0 or active[i : i + w].mean() < 0.3:
            sc = 0.0
        else:
            p = np.abs(np.fft.rfft((seg - seg.mean()) * np.hanning(w))) ** 2
            mod = float(p[syl].sum()) / max(float(p[allb].sum()), 1e-9)
            v = float(voice[i + w // 2])
            pf = float(pause[i + w // 2])
            sc = 0.45 * np.clip((v - 0.4) / 0.4, 0, 1) + 0.4 * np.clip((mod - 0.15) / 0.35, 0, 1) + 0.15 * (1.0 if 0.05 <= pf <= 0.6 else 0.0)
        centers.append((i + w / 2) / fps)
        scores.append(sc)
    cs, sc_arr = np.array(centers), np.array(scores)
    # expand window scores to a per-50Hz mask
    t = np.arange(n) / fps
    sc_f = np.interp(t, cs, sc_arr) if len(cs) > 1 else np.zeros(n)
    mask = (sc_f >= 0.5) & active
    mask = smooth(mask.astype(float), int(fps * 0.5)) >= 0.5
    segs = []
    for a, b in runs(mask, fps, min_len=0.8, merge_gap=1.0):  # bridge phrase-level pauses so ducking doesn't pump
        sl = slice(int(a * fps), max(int(b * fps), int(a * fps) + 1))
        conf = round(float(min(0.6, 0.25 + 0.5 * np.mean(sc_f[sl]))), 2)
        segs.append(SpeechSegment(start_time=round(a, 2), end_time=round(b, 2), confidence=conf, source="estimated",
                                  provenance="voice-band share + 2.5-7 Hz envelope modulation + pause structure (heuristic VAD)"))
    return segs, {"noise_floor_db": round(floor, 1), "peak_db": round(peak, 1), "windows": len(scores)}
