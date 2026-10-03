"""Music feature extraction (tempo, beats, key, loudness, energy lifts) with numpy + ffmpeg.

Everything here is `estimated`: classical DSP (spectral-flux onset envelope, comb-weighted
autocorrelation tempo, Ellis-style dynamic-programming beat tracking, Krumhansl key profiles).
Downbeats assume 4/4. Providers that ship exact beat metadata should supply it instead
(source="provider"/"detected").
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ..analysis.media import decode_mono, run, which
from ..schemas import Measured, MusicFeatures

SR = 22050
N_FFT = 2048
HOP = 512
FPS = SR / HOP
# Spectral-flux peaks lead the true onset by roughly half a frame hop; measured against the
# procedural generator in tests/unit/test_music_features.py.
FRAME_OFFSET = 0.066
MAX_ANALYZE_SECONDS = 1200

_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _stft_stats(path: Path, settings, max_seconds):
    win = np.hanning(N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    kb = (freqs >= 65) & (freqs <= 2100)
    pcs = (np.round(12 * np.log2(np.maximum(freqs[kb], 1) / 440.0) + 69).astype(int)) % 12
    M = np.zeros((kb.sum(), 12), np.float32)
    M[np.arange(kb.sum()), pcs] = 1.0
    low, mid, high = freqs < 300, (freqs >= 300) & (freqs < 3400), freqs >= 3400
    flux, lowflux, rms, cent = [], [], [], []
    chroma = np.zeros(12)
    band = np.zeros(3)
    prev = None
    left = np.zeros(0, np.float32)
    for chunk in decode_mono(path, SR, settings, max_seconds=max_seconds):
        buf = np.concatenate([left, chunk])
        if len(buf) < N_FFT:
            left = buf
            continue
        n = (len(buf) - N_FFT) // HOP + 1
        fr = np.lib.stride_tricks.sliding_window_view(buf, N_FFT)[::HOP][:n]
        mag = np.abs(np.fft.rfft(fr * win, axis=1)).astype(np.float32)
        lm = np.log1p(100 * mag)
        d = np.diff(lm, axis=0, prepend=(prev if prev is not None else lm[:1]))
        d = np.maximum(d, 0)
        flux.append(d.sum(1))
        lowflux.append(d[:, low].sum(1))
        r = np.sqrt(np.mean(fr**2, axis=1) + 1e-12)
        rms.append(r)
        e = mag**2
        cent.append((e * freqs).sum(1) / (e.sum(1) + 1e-9))
        w = r / (r.max() + 1e-9)
        chroma += ((mag[:, kb] * w[:, None]) @ M).sum(0)
        band += np.array([e[:, low].sum(), e[:, mid].sum(), e[:, high].sum()])
        prev = lm[-1:]
        left = buf[n * HOP :]
    if not flux:
        return None
    return (np.concatenate(flux), np.concatenate(lowflux), np.concatenate(rms), np.concatenate(cent), chroma, band)


def estimate_tempo(env: np.ndarray, fps: float = FPS) -> tuple[float, float, list[float]]:
    e = env - env.mean()
    if len(e) < fps * 4 or e.std() < 1e-6:
        return 0.0, 0.0, []
    e = e / e.std()
    n = len(e)
    f = np.fft.rfft(e, 2 * n)
    acf = np.fft.irfft(f * np.conj(f))[:n]
    acf = acf / (acf[0] + 1e-9)
    bpms = np.arange(55.0, 200.0, 0.25)
    lags = fps * 60.0 / bpms

    def at(lag):
        lag = np.asarray(lag)
        ok = lag < n - 1
        out = np.zeros_like(lag, dtype=float)
        li = np.floor(lag[ok]).astype(int)
        fr = lag[ok] - li
        out[ok] = acf[li] * (1 - fr) + acf[li + 1] * fr
        return out

    comb = at(lags) + 0.5 * at(2 * lags) + 0.25 * at(4 * lags)
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.8) ** 2)
    score = comb * prior
    i = int(np.argmax(score))
    bpm = float(bpms[i])
    conf = float(np.clip(0.25 + 1.6 * at(np.array([fps * 60.0 / bpm]))[0], 0.1, 0.9))
    alts = [round(b, 1) for b in (bpm * 2, bpm / 2) if 55 <= b <= 200]
    return round(bpm, 1), round(conf, 2), alts


def track_beats(env: np.ndarray, bpm: float, fps: float = FPS) -> np.ndarray:
    """Ellis (2007) dynamic-programming beat tracker."""
    if bpm <= 0 or len(env) < 8:
        return np.zeros(0)
    e = env / (env.std() + 1e-9)
    p = fps * 60.0 / bpm
    n = len(e)
    score = e.copy()
    back = np.full(n, -1, dtype=int)
    lo, hi = int(round(p / 2)), int(round(p * 2))
    offs = np.arange(lo, hi + 1)
    pen = -100.0 * np.log(offs / p) ** 2
    for i in range(n):
        j = i - offs
        ok = j >= 0
        if not ok.any():
            continue
        cand = score[j[ok]] + pen[ok]
        k = int(np.argmax(cand))
        score[i] = e[i] + cand[k]
        back[i] = j[ok][k]
    end = int(np.argmax(score[max(0, n - int(p)) :])) + max(0, n - int(p))
    beats = [end]
    while back[beats[-1]] >= 0:
        beats.append(int(back[beats[-1]]))
    return np.array(beats[::-1]) / fps + FRAME_OFFSET


def estimate_key(chroma: np.ndarray) -> tuple[str | None, float]:
    if chroma.sum() <= 0:
        return None, 0.0
    c = chroma / chroma.sum()
    best = []
    for prof, mode in ((_KS_MAJOR, "major"), (_KS_MINOR, "minor")):
        for k in range(12):
            r = np.corrcoef(c, np.roll(prof, k))[0, 1]
            best.append((r, f"{_NOTES[k]} {mode}"))
    best.sort(reverse=True)
    return best[0][1], round(float(np.clip(best[0][0] - best[1][0] + 0.3, 0, 0.9)), 2)


def measure_loudness(path: Path, settings, rms_hint: float | None = None) -> tuple[float | None, str | None]:
    cp = run([which("ffmpeg", settings), "-nostats", "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"], timeout=1200, check=False)
    m = re.findall(r"I:\s+(-?[0-9.]+)\s+LUFS", cp.stderr)
    if m:
        return float(m[-1]), "ebur128"
    if rms_hint:
        return round(20 * np.log10(rms_hint + 1e-9) - 0.7, 1), "rms_estimate"
    return None, None


def analyze_music(path: Path, settings=None, *, provider_beats: dict | None = None, loudness: bool = True, max_seconds: float | None = None) -> MusicFeatures:
    st = _stft_stats(path, settings, max_seconds or MAX_ANALYZE_SECONDS)
    if st is None:
        from ..errors import UnsupportedMediaError

        raise UnsupportedMediaError(f"'{path.name}' contains no decodable audio.", "Supported audio: wav, mp3, flac, ogg, m4a, aac, opus.")
    flux, lowflux, rms, cent, chroma, band = st
    dur = len(rms) * HOP / SR
    db = 20 * np.log10(rms + 1e-9)
    # per-second energy curve (absolute scale: -40 dBFS -> 0, -10 dBFS -> 1).
    # Averaged in the power domain: averaging dB would let the near-silent frames between
    # transients dominate, which reads a sparse percussive track as silence.
    per = int(round(FPS))
    nsec = max(1, len(rms) // per)
    sec_rms = (np.sqrt((rms[: nsec * per] ** 2).reshape(nsec, per).mean(1)) if len(rms) >= per
               else np.sqrt((rms**2).mean(keepdims=True)))
    sec_db = 20 * np.log10(sec_rms + 1e-9)
    curve = np.clip((sec_db + 40) / 30, 0, 1)
    energy_curve = [(float(i), round(float(v), 3)) for i, v in enumerate(curve)]

    bpm_v, conf, alts = estimate_tempo(flux)
    beats = track_beats(flux, bpm_v) if bpm_v else np.zeros(0)
    downbeats = np.zeros(0)
    if len(beats) >= 8:
        idx = (np.clip((beats * FPS).astype(int), 0, len(lowflux) - 1))
        strengths = [float(lowflux[idx[k::4]].mean()) for k in range(4)]
        k = int(np.argmax(strengths))
        downbeats = beats[k::4]
    lifts = _find_lifts(rms, downbeats if len(downbeats) >= 4 else None, dur)
    key, kconf = estimate_key(chroma)
    lufs, lsrc = measure_loudness(path, settings, float(np.sqrt(np.mean(rms**2)))) if loudness else (None, None)
    tot = band.sum() + 1e-9
    feats = MusicFeatures(
        duration=round(dur, 3),
        bpm=Measured(value=bpm_v, source="estimated", confidence=conf) if bpm_v else None,
        bpm_alternatives=alts,
        key=key, key_confidence=kconf if key else None,
        loudness_lufs=lufs, loudness_source=lsrc,  # type: ignore[arg-type]
        beats=[round(float(b), 3) for b in beats[:20000]],
        downbeats=[round(float(b), 3) for b in downbeats[:5000]],
        beats_source="estimated",
        lifts=lifts, energy_curve=energy_curve[:3600],
        spectral_centroid_hz=round(float(np.average(cent, weights=rms + 1e-9)), 1),
        band_ratios={"low": round(float(band[0] / tot), 3), "mid": round(float(band[1] / tot), 3), "high": round(float(band[2] / tot), 3)},
        dynamic_range_db=round(float(np.percentile(sec_db, 95) - np.percentile(sec_db, 10)), 1) if len(sec_db) > 2 else None,
    )
    if provider_beats:
        feats = feats.model_copy(update=provider_beats)
    return feats


def _find_lifts(rms: np.ndarray, downbeats: np.ndarray | None, dur: float) -> list[tuple[float, float]]:
    """Times where energy rises markedly relative to the preceding few bars (drops/section starts)."""
    if downbeats is not None and len(downbeats) >= 6:
        marks = downbeats
    else:
        marks = np.arange(0, dur, 2.0)
    if len(marks) < 4:
        return []
    edges = np.append(marks, dur)
    bar = np.array([rms[int(a * FPS) : max(int(b * FPS), int(a * FPS) + 1)].mean() if int(a * FPS) < len(rms) else 0 for a, b in zip(edges[:-1], edges[1:], strict=True)])
    out = []
    for i in range(2, len(bar) - 1):
        prev = bar[max(0, i - 4) : i].mean()
        nxt = bar[i : i + 2].mean()
        s = (nxt - prev) / (bar.mean() + 1e-9)
        if s > 0.2 and nxt >= bar[max(0, i - 1)]:
            out.append((round(float(marks[i]), 3), round(float(min(s, 3.0)), 3)))
    out.sort(key=lambda x: -x[1])
    # keep distinct lifts (>= 4 s apart)
    kept: list[tuple[float, float]] = []
    for t, s in out:
        if all(abs(t - k[0]) >= 4 for k in kept):
            kept.append((t, s))
        if len(kept) >= 6:
            break
    return kept
