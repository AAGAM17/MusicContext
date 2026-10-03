"""Music generation providers.

`ProceduralGenerationProvider` exists so `musiccontext generate` works with zero
configuration and zero network access. It renders the brief it is handed (tempo, key,
energy arc, peak moments) with numpy, writes 16-bit PCM, and then measures the result
with the same analyzer used on third-party audio — so the features it reports are
observed, never claimed.

`HTTPGenerationProvider` is a documented template for a real cloud generator. It ships
no vendor endpoint, model name or request shape: a subclass supplies those. Nothing in
this module registers itself with the engine except through `providers.registry`.
"""

from __future__ import annotations

import hashlib
import json
import re
import wave
from pathlib import Path

import numpy as np

from ..errors import ProviderError, ProviderUnavailableError
from ..schemas import (
    MusicCandidate,
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicLicense,
    ProviderCapability,
)
from ..security.content import sanitize_text
from .base import MusicGenerationProvider

SR = 44100
PEAK_DBFS = -1.0
DOCS = "docs/providers/"

_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_MAJOR = (0, 2, 4, 5, 7, 9, 11)
_MINOR = (0, 2, 3, 5, 7, 8, 10)
# Degree loops that sound like themselves in four bars: I-V-vi-IV and i-VI-III-VII.
_MAJOR_PROG = (0, 4, 5, 3)
_MINOR_PROG = (0, 5, 2, 6)
_KEY_RE = re.compile(r"^\s*([A-Ga-g])\s*([#b♯♭]?)\s*(.*)$")
# How far below the peak level the quietest sections sit. The analyzer's energy scale spans 30 dB.
DYNAMICS_DB = 20.0
# Section labels nudge the arrangement density around the energy curve.
_SECTION_GAIN = (
    ("intro", -0.18), ("outro", -0.22), ("resolution", -0.14), ("release", -0.10),
    ("build", 0.06), ("rise", 0.06), ("peak", 0.12), ("drop", 0.12), ("chorus", 0.08), ("hook", 0.08),
)


def _parse_key(key: str | None) -> tuple[int, tuple[int, ...], str]:
    """'F# minor' -> (6, _MINOR, 'F# minor'). Unparseable input falls back to A minor."""
    m = _KEY_RE.match(key or "")
    if not m:
        return 9, _MINOR, "A minor"
    pc = _PITCH[m.group(1).upper()] + (1 if m.group(2) in ("#", "♯") else -1 if m.group(2) else 0)
    minor = "min" in m.group(3).lower() or m.group(3).strip() in ("m", "M-")
    scale = _MINOR if minor else _MAJOR
    name = f"{m.group(1).upper()}{m.group(2)} {'minor' if minor else 'major'}".replace("  ", " ")
    return pc % 12, scale, name


def _triad(scale: tuple[int, ...], degree: int) -> list[int]:
    return [scale[(degree + k) % 7] + 12 * ((degree + k) // 7) for k in (0, 2, 4)]


def _freq(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def _energy_at(curve: list[tuple[float, float]], times: np.ndarray, duration: float) -> np.ndarray:
    if curve:
        pts = sorted((float(t), float(np.clip(e, 0.0, 1.0))) for t, e in curve)
        return np.interp(times, [p[0] for p in pts], [p[1] for p in pts])
    # No curve supplied: a generic arc that rises to a late peak and eases out.
    x = np.clip(times / max(duration, 1e-6), 0.0, 1.0)
    return 0.35 + 0.5 * np.sin(np.pi * np.clip(x / 0.72, 0, 1) ** 0.9) * (1 - 0.35 * np.clip((x - 0.72) / 0.28, 0, 1))


def _section_bonus(structure: list[str], times: np.ndarray, duration: float) -> np.ndarray:
    if not structure:
        return np.zeros_like(times)
    span = duration / len(structure)
    idx = np.clip((times / max(span, 1e-6)).astype(int), 0, len(structure) - 1)
    gains = np.array([next((g for k, g in _SECTION_GAIN if k in (lbl or "").lower()), 0.0) for lbl in structure])
    return gains[idx]


def _add(buf: np.ndarray, start: int, x: np.ndarray) -> None:
    i, j = max(0, start), min(len(buf), start + len(x))
    if j > i:
        buf[i:j] += x[i - start : j - start]


def _kick(punch: float) -> np.ndarray:
    """Percussive hit. Its body sweeps 420 -> 190 Hz, i.e. deliberately *above* the analyzer's
    low band (<300 Hz), so the only strong sub-300 Hz events in the mix are the bar accents."""
    t = np.arange(int(0.16 * SR)) / SR
    body = np.sin(2 * np.pi * (420 * np.exp(-t * 15) + 190) * t) * np.exp(-t * 22)
    click = np.zeros_like(t)
    k = int(0.004 * SR)
    click[:k] = np.sin(2 * np.pi * 2300 * t[:k]) * np.linspace(1, 0, k) ** 2
    return body * (1 + punch) + 0.25 * click


def _bass_register(midi: int) -> int:
    """Drop the chord root into a 55-110 Hz bass register.

    A fixed two-octave drop would put low keys near 33 Hz, where the swell is too close to the
    bottom of the analyzer's low band to mark the bar reliably. Octave-folding keeps every key in
    the same audible (and measurable) register.
    """
    m = midi - 24
    while _freq(m) < 50.0:
        m += 12
    return m


def _sub(freq: float, dur: float) -> np.ndarray:
    """The bass: one swelling low note per bar, which also marks the downbeat.

    The analyzer picks the bar phase from its low-band spectral flux sampled ~3 frames after each
    beat it found. A transient has decayed by then and reads as *falling* low energy, so a sharp
    accent is inaudible to the measurement however loud it is. A ~250 ms rising attack is still
    rising when the analyzer looks, which is what makes the downbeat measurable and not merely
    audible. Keeping every other layer above 300 Hz leaves this voice alone in the low band.
    """
    n = max(8, int(dur * SR))
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * 1.5 * freq * t)
    a = max(8, int(min(0.25, dur * 0.7) * SR))
    env = np.exp(-np.maximum(t - a / SR, 0.0) * 3.0)
    env[:a] *= np.linspace(0, 1, a)
    return x * env


def _pluck(freq: float, dur: float, bright: float = 1.0) -> np.ndarray:
    n = max(8, int(dur * SR))
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * freq * t) + 0.3 * bright * np.sin(2 * np.pi * 2 * freq * t) + 0.12 * bright * np.sin(2 * np.pi * 3 * freq * t)
    a = max(4, int(0.012 * SR))
    env = np.exp(-t * 7.0)
    env[:a] *= np.linspace(0, 1, a)
    return x * env


def _bass(freq: float, dur: float) -> np.ndarray:
    n = max(8, int(dur * SR))
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * freq * t) + 0.22 * np.sin(2 * np.pi * 2 * freq * t)
    a = max(4, int(0.008 * SR))
    env = np.exp(-t * 2.6)
    env[:a] *= np.linspace(0, 1, a)
    r = max(4, int(0.02 * SR))
    env[-r:] *= np.linspace(1, 0, r)
    return x * env


def _pad(freqs: list[float], dur: float, rng: np.random.Generator) -> np.ndarray:
    n = max(16, int(dur * SR))
    t = np.arange(n) / SR
    f = np.array(freqs)[:, None]
    # sine plus a soft triangle partial so the pad has body without a harsh edge
    x = (np.sin(2 * np.pi * f * t) + 0.16 * np.sin(2 * np.pi * 3 * f * t) / 3).sum(0) / len(freqs)
    x *= 1 + 0.02 * np.sin(2 * np.pi * rng.uniform(0.15, 0.45) * t)  # slow drift, seeded
    env = np.ones(n)
    a = max(8, int(min(0.18, dur * 0.35) * SR))
    r = max(8, int(min(0.22, dur * 0.35) * SR))
    env[:a] = np.linspace(0, 1, a) ** 1.4
    env[-r:] = np.linspace(1, 0, r) ** 1.2
    return x * env


def _hat(dur: float, rng: np.random.Generator) -> np.ndarray:
    n = max(8, int(dur * SR))
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    # crude one-pole high-pass: the difference of successive samples keeps only the top end
    hp = np.diff(noise, prepend=noise[0])
    return hp * np.exp(-t * 55.0)


def _render(req: MusicGenerationRequest, seed: int) -> tuple[np.ndarray, dict]:
    """Render the request to float32 PCM plus the grid metadata that proves the brief was followed."""
    rng = np.random.default_rng(seed)
    duration = float(req.duration)
    bpm = float(req.bpm or 100.0)
    # ponytail: the grid is honoured exactly at any tempo, but music.features' tempo prior is centred
    # on 120 BPM and will report half-time above ~180 BPM. Measured BPM tracks the request to ~1.5 BPM
    # over 60-160. Fix belongs in estimate_tempo (a wider prior), not in a busier arrangement here.
    beat = 60.0 / bpm
    bar = 4 * beat
    pc, scale, key_name = _parse_key(req.key)

    # Phase the bar grid so the first requested peak lands exactly on a downbeat. Tempo is set by
    # the beat period, so shifting phase honours the requested BPM exactly while placing the accent.
    peaks = sorted({round(float(p), 4) for p in req.peak_times if 0.0 <= float(p) < duration})
    phase = (peaks[0] % bar) if peaks else 0.0
    i0 = -(int(phase / beat))
    n_beats = int(np.ceil((duration - phase) / beat)) + 1
    idx = np.arange(i0, n_beats + 1)
    beat_times = phase + idx * beat
    keep = (beat_times >= -1e-9) & (beat_times < duration)
    idx, beat_times = idx[keep], np.maximum(beat_times[keep], 0.0)
    downbeats = beat_times[idx % 4 == 0]

    def nearest_downbeat(t: float) -> float:
        return float(downbeats[int(np.argmin(np.abs(downbeats - t)))]) if len(downbeats) else 0.0

    peak_grid = sorted({nearest_downbeat(p) for p in peaks})

    e = _energy_at(list(req.energy_curve), beat_times, duration) + _section_bonus(list(req.structure), beat_times, duration)
    for p in peak_grid:  # a peak is a peak: lift the two bars that land on it
        e = np.where((beat_times >= p - 1e-6) & (beat_times < p + 2 * bar), np.maximum(e, 0.78), e)
    e = np.clip(e, 0.22, 1.0)

    n = int(round(duration * SR))
    tail = int(0.5 * SR)
    out = np.zeros(n + tail)

    # --- pad: one chord per bar, chord tones from a diatonic loop in the requested key
    prog = _MINOR_PROG if scale is _MINOR else _MAJOR_PROG
    chord_for_bar: dict[int, list[int]] = {}
    for b, t0 in enumerate(downbeats):
        deg = prog[b % len(prog)]
        semis = _triad(scale, deg)
        chord_for_bar[b] = [48 + pc + s for s in semis]
        eb = float(e[np.argmin(np.abs(beat_times - t0))])
        pad = _pad([_freq(m + 12) for m in chord_for_bar[b]], bar, rng) * (0.20 + 0.14 * eb)
        _add(out, int(round(t0 * SR)), pad.astype(np.float64))

    # --- rhythm + melodic layers on the beat grid
    kick_plain = _kick(0.0)
    for k, (i, t0) in enumerate(zip(idx, beat_times, strict=True)):
        eb = float(e[k])
        s0 = int(round(t0 * SR))
        is_down = i % 4 == 0
        bar_no = int(np.floor((t0 - phase) / bar + 1e-9)) - int(np.floor(-phase / bar + 1e-9)) if bar else 0
        chord = chord_for_bar.get(max(0, min(bar_no, len(downbeats) - 1)), [48 + pc, 48 + pc + 4, 48 + pc + 7])

        g = 0.55 + 0.32 * eb
        on_peak = any(abs(t0 - p) < 1e-6 for p in peak_grid)
        _add(out, s0, kick_plain * (g * (1.55 if on_peak else 1.15 if is_down else 1.0)))
        if is_down:  # the bar's bass note, and the only strong low-band event in the mix
            _add(out, s0, _sub(_freq(_bass_register(chord[0])), min(bar, 1.2)) * ((0.9 + 0.2 * eb) * (1.35 if on_peak else 1.0)))

        if not is_down and (i % 2 == 0 or eb > 0.55):
            _add(out, s0, _bass(_freq(chord[0] - 12), beat * 0.95) * (0.22 + 0.14 * eb))

        # Off-beat hat, but only above ~95 BPM: at slow tempos a half-beat pulse sits near 120 BPM,
        # where the analyzer's tempo prior happily reports it as the tempo instead.
        if eb > 0.45 and bpm >= 95.0:
            _add(out, s0 + int(round(beat * 0.5 * SR)), _hat(0.07, rng) * (0.016 + 0.020 * eb))

        # ponytail: the pluck arpeggiates on the beat, not on eighths. Off-beat onsets as loud as the
        # kick make the beat tracker lock to half a beat and report double tempo; the brief's honesty
        # check (measured BPM == requested BPM) matters more than a busier pattern.
        if eb > 0.55:
            note = chord[k % 3] + 12 * (1 + (k // 3) % 2)
            _add(out, s0, _pluck(_freq(note), beat * 0.8) * (0.085 + 0.055 * eb))

    # --- the new layer that enters exactly on each peak: an octave bell on the chord root
    for p in peak_grid:
        b = int(np.argmin(np.abs(downbeats - p)))
        root = chord_for_bar.get(b, [48 + pc])[0]
        _add(out, int(round(p * SR)), _pluck(_freq(root + 24), min(bar, 1.6), bright=1.6) * 0.26)

    # Dynamics: the layers above only change *density*, and a kick on every beat keeps the measured level
    # flat. Follow the energy curve in level too, so a sparse intro is audibly and measurably quieter.
    lvl = np.clip((e - 0.22) / 0.78, 0.0, 1.0)
    env_db = -DYNAMICS_DB * (1.0 - np.interp(np.arange(len(out)) / SR, beat_times, lvl))
    out *= 10 ** (env_db / 20)

    out = out[:n]
    fi = min(int(0.25 * SR), n // 8)
    fo = min(int(1.5 * SR), n // 4)
    out[:fi] *= np.linspace(0, 1, fi)
    out[n - fo :] *= np.linspace(1, 0, fo) ** 1.3
    peak = float(np.max(np.abs(out)))
    out *= (10 ** (PEAK_DBFS / 20)) / (peak if peak > 1e-9 else 1.0)

    plan = {
        "key": key_name, "bpm": bpm, "beat_count": int(len(beat_times)), "bar_count": int(len(downbeats)),
        "grid_phase": round(phase, 4), "peaks_requested": peaks, "peaks_on_grid": [round(p, 4) for p in peak_grid],
        # one offset per *requested* peak: peak_grid is deduplicated, so zipping the two would misalign
        "peak_offsets_ms": [round((nearest_downbeat(p) - p) * 1000, 1) for p in peaks],
        "downbeats": [round(float(d), 4) for d in downbeats],
    }
    return out.astype(np.float32), plan


def _write_wav(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.round(np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


class ProceduralGenerationProvider(MusicGenerationProvider):
    """Offline synthesizer. Always available: it is the zero-configuration fallback.

    Everything it emits is synthesized from scratch by this code — no samples, no model
    weights, no third-party audio — which is why it can state a verified, commercial-use
    license for its own output.
    """

    name = "procedural"
    kinds = ("generation",)
    requires_credentials = False
    sends_media_off_device = False
    data_handling = "Runs entirely on this machine; nothing is uploaded."

    def capability(self, settings) -> ProviderCapability:
        return ProviderCapability(
            name=self.name,
            kinds=["generation"],
            available=True,
            requires_credentials=False,
            sends_media_off_device=False,
            data_handling=self.data_handling,
            supports={"instrumental_only": True, "deterministic_seed": True, "offline": True, "vocals": False},
            notes="Offline instrumental synthesizer (numpy). Reproducible from its seed; honours duration, "
                  "BPM, key, energy curve and peak times.",
        )

    def generate(self, req: MusicGenerationRequest, settings, dest: Path | None = None) -> MusicGenerationResult:
        from ..music.features import analyze_music

        seed = int(req.seed) if req.seed is not None else 0
        track_id = _track_id(req, seed)
        audio, plan = _render(req, seed)
        path = Path(dest) if dest else Path(settings.home) / "generated" / f"{self.name}-{track_id}.wav"
        _write_wav(path, audio)

        rendered = len(audio) / SR
        features = analyze_music(path, settings)
        measured = f"{features.bpm.value:.1f} BPM ({features.bpm.source})" if features.bpm else "no stable tempo"
        provenance = (
            f"Synthesized from scratch on this machine by MusicContext's procedural generator "
            f"(numpy additive synthesis, seed={seed}, {SR} Hz 16-bit PCM mono, {rendered:.2f} s). "
            f"No samples, no model weights and no third-party audio were used. "
            f"Requested {plan['bpm']:.1f} BPM in {plan['key']}; measured {measured}."
        )
        lic = MusicLicense(
            provider=self.name,
            license="generated-output",
            commercial_use="yes",
            attribution_required=False,
            verified=True,
            verified_by="generated locally by MusicContext",
            download_restrictions=None,
            source_url=None,
        )
        meta: dict[str, str] = {
            "seed": str(seed),
            "requested_bpm": f"{plan['bpm']:.1f}",
            "key": str(plan["key"]),
            "grid_phase_s": str(plan["grid_phase"]),
            "bars": str(plan["bar_count"]),
            "peaks_requested": ", ".join(f"{p:.3f}" for p in plan["peaks_requested"]) or "none",
            "peaks_on_grid": ", ".join(f"{p:.3f}" for p in plan["peaks_on_grid"]) or "none",
            "peak_offsets_ms": ", ".join(str(o) for o in plan["peak_offsets_ms"]) or "n/a",
            "structure": ", ".join(sanitize_text(s, 40) for s in req.structure) or "unstructured",
            "synthesis": "numpy additive synthesis; pad, bass, kick, hat and pluck layers gated by the energy curve",
            "license_basis": "output is synthesized from scratch by this code, so MusicContext can license it",
        }
        if req.vocals != "none":
            meta["vocals_note"] = f"'{req.vocals}' was requested; this provider only produces instrumental music."
        if len(plan["peaks_on_grid"]) > 1:
            meta["peak_note"] = ("Only the first peak can sit exactly on a bar line at a constant tempo; "
                                 "the rest are snapped to the nearest downbeat (see peak_offsets_ms).")

        candidate = MusicCandidate(
            id=track_id, provider=self.name,
            title=f"Procedural {plan['key']} {plan['bpm']:.0f} BPM",
            artist="MusicContext procedural generator",
            uri=str(path),
            tags=[*(sanitize_text(s, 40).lower() for s in req.style), *(sanitize_text(m, 40).lower() for m in req.mood), "instrumental", "generated"],
            features=features, license=lic, metadata=dict(meta),
        )
        return MusicGenerationResult(
            provider=self.name, track_id=track_id, duration=round(rendered, 3), candidate=candidate,
            artifact_path=str(path), metadata=meta, provenance=provenance, license=lic, status="completed",
        )


def _track_id(req: MusicGenerationRequest, seed: int) -> str:
    """Content address: the same brief and seed always name the same track."""
    blob = json.dumps(req.model_dump(mode="json"), sort_keys=True, default=str) + f"|{seed}"
    return "proc-" + hashlib.blake2b(blob.encode(), digest_size=6).hexdigest()


class HTTPGenerationProvider(MusicGenerationProvider):
    """Template for a cloud music generator. Not registered, not usable until subclassed.

    MusicContext ships no vendor integration: inventing an endpoint, a model name or a
    request shape would be a lie about an API we have not tested. A subclass supplies all
    of it::

        class AcmeMusic(HTTPGenerationProvider):
            name = "acme"
            endpoint = "https://api.acme.example/v1/music"   # from the vendor's own docs
            credential_env = "MUSICCONTEXT_ACME_API_KEY"

            def _submit(self, request, settings) -> dict:
                '''POST the prompt, return the vendor's job object as a dict.'''

            def _poll(self, job, settings) -> dict:
                '''Poll until the job is finished; return the final payload.'''

            def _artifact_url(self, payload) -> str:
                '''Pull the audio URL out of that payload.'''

    Inherited for free: credential/local-only gating in `capability()`, the unavailable-provider
    error, and a `download()` that goes through the SSRF-safe, size-capped fetcher and then
    measures the real features of what arrived. See docs/providers/ for the full contract.

    Everything a subclass returns is UNTRUSTED: sanitize it before it reaches a model, never
    let it become an instruction, and never put the credential in a result, log or error.
    """

    endpoint: str  # no default: a subclass must name its own vendor's endpoint
    credential_env: str | None = None
    requires_credentials = True
    kinds = ("generation",)
    sends_media_off_device = False
    data_handling = "Only the text prompt and the numeric brief leave this machine; your media is never uploaded."

    def _env_name(self) -> str:
        return self.credential_env or f"MUSICCONTEXT_{self.name.upper().replace('-', '_')}_API_KEY"

    def capability(self, settings) -> ProviderCapability:
        has_key = bool(settings.api_key_for(self.name))
        local_only = bool(getattr(settings, "local_only", False))
        if local_only:
            notes = "Disabled because local_only is set (MUSICCONTEXT_LOCAL_ONLY=1); no request leaves this machine."
        elif not has_key:
            notes = f"Set {self._env_name()} to enable it. See {DOCS} for the subclass contract."
        else:
            notes = f"Configured. Sends the prompt to {getattr(self, 'endpoint', '(endpoint not set)')}."
        return ProviderCapability(
            name=self.name,
            kinds=["generation"],
            available=has_key and not local_only,
            requires_credentials=True,
            credentials_present=has_key,
            credential_env=self._env_name(),
            sends_media_off_device=self.sends_media_off_device,
            data_handling=self.data_handling,
            supports={"offline": False, "deterministic_seed": False},
            notes=notes,
        )

    def generate(self, req: MusicGenerationRequest, settings, dest: Path | None = None) -> MusicGenerationResult:
        cap = self.capability(settings)
        if not cap.available:
            hint = (cap.notes or f"Set {self._env_name()} to enable it.") + f" Provider template: {DOCS}"
            raise ProviderUnavailableError(f"Provider '{self.name}' is not configured.", hint)
        job = self._submit(req, settings)
        payload = self._poll(job, settings)
        url = self._artifact_url(payload)
        if not url:
            raise ProviderError(f"Provider '{self.name}' returned no artifact URL.", f"Check _artifact_url(); see {DOCS}.")
        candidate = MusicCandidate(
            id=sanitize_text(payload.get("id") or job.get("id") or "", 80) or "unknown",
            provider=self.name, uri=sanitize_text(url, 500), title=sanitize_text(payload.get("title"), 200),
        )
        return MusicGenerationResult(
            provider=self.name, track_id=candidate.id, duration=float(req.duration), candidate=candidate,
            artifact_path=None, provenance=f"Generated remotely by '{self.name}' from a text prompt; "
            "audio is not downloaded until download() is called.", status="completed",
        )

    # --- hooks a subclass implements -------------------------------------------------
    def _submit(self, request: MusicGenerationRequest, settings) -> dict:
        raise NotImplementedError(f"{type(self).__name__}._submit() is not implemented; see {DOCS} for the subclass contract.")

    def _poll(self, job: dict, settings) -> dict:
        raise NotImplementedError(f"{type(self).__name__}._poll() is not implemented; see {DOCS} for the subclass contract.")

    def _artifact_url(self, payload: dict) -> str:
        raise NotImplementedError(f"{type(self).__name__}._artifact_url() is not implemented; see {DOCS} for the subclass contract.")

    def download(self, result: MusicGenerationResult, dest: Path, settings) -> Path:
        """Fetch the generated audio through the SSRF-safe fetcher, then measure what arrived."""
        from ..music.features import analyze_music
        from ..security.network import download as net_download
        from ..security.network import is_url
        from .search import sandbox_output

        url = result.candidate.uri or ""
        if not is_url(url):
            return super().download(result, dest, settings)
        target = sandbox_output(dest, settings, force=True)
        with net_download(url, settings) as tmp:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tmp.read_bytes())
        result.candidate.features = analyze_music(target, settings)  # real features, not the provider's claim
        result.candidate.uri = str(target)
        result.artifact_path = str(target)
        return target
