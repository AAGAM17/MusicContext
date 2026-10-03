"""Style and mood reasoning from observable video statistics.

Each style/mood has a small feature prototype. The video's features are compared to each
prototype (weighted distance); adjustments for voiceover and platform are applied on top.
The tables are plain data so they can be audited and extended.
"""

from __future__ import annotations

from dataclasses import dataclass

# prototype: energy, pace(0 slow..1 fast), brightness, saturation, warmth(-1..1)
STYLES: dict[str, dict] = {
    "cinematic": dict(p=(0.60, 0.5, 0.40, 0.55, 0.0), tex=["hybrid", "spacious"], inst=["strings", "piano", "sub_bass", "cinematic_percussion", "synth_pads"], speech=-0.05),
    "documentary": dict(p=(0.30, 0.3, 0.55, 0.40, 0.05), tex=["organic", "atmospheric"], inst=["piano", "soft_strings", "acoustic_guitar", "light_percussion"], speech=0.10),
    "corporate": dict(p=(0.45, 0.5, 0.70, 0.45, 0.05), tex=["dry", "hybrid"], inst=["piano", "acoustic_guitar", "light_percussion", "plucks"], speech=0.10),
    "technology": dict(p=(0.50, 0.5, 0.45, 0.50, -0.15), tex=["synthetic", "dry"], inst=["synth", "plucks", "subtle_percussion", "sub_bass"], speech=0.05),
    "luxury": dict(p=(0.30, 0.25, 0.30, 0.25, 0.05), tex=["spacious", "hybrid"], inst=["piano", "strings", "soft_pads", "sub_bass"], speech=0.05),
    "emotional": dict(p=(0.35, 0.3, 0.50, 0.45, 0.15), tex=["acoustic", "organic"], inst=["piano", "strings", "acoustic_guitar"], speech=0.0),
    "energetic": dict(p=(0.85, 0.85, 0.60, 0.75, 0.05), tex=["synthetic", "hybrid"], inst=["drums", "bass", "synth", "claps"], speech=-0.25),
    "playful": dict(p=(0.65, 0.7, 0.75, 0.80, 0.15), tex=["organic", "dry"], inst=["ukulele", "marimba", "claps", "pizzicato_strings"], speech=-0.05),
    "suspenseful": dict(p=(0.45, 0.4, 0.15, 0.30, -0.10), tex=["atmospheric", "spacious"], inst=["low_strings", "pulses", "sub_bass", "textures"], speech=-0.05),
    "ambient": dict(p=(0.15, 0.1, 0.50, 0.35, 0.0), tex=["atmospheric", "spacious"], inst=["pads", "textures", "soft_piano"], speech=0.15),
    "minimal": dict(p=(0.30, 0.35, 0.60, 0.30, 0.0), tex=["dry", "spacious"], inst=["piano", "soft_pads", "subtle_percussion", "plucks"], speech=0.15),
    "orchestral": dict(p=(0.65, 0.5, 0.45, 0.50, 0.05), tex=["acoustic", "spacious"], inst=["strings", "brass", "timpani", "woodwinds"], speech=-0.10),
    "electronic": dict(p=(0.70, 0.7, 0.40, 0.65, -0.15), tex=["synthetic"], inst=["synth", "drum_machine", "bass", "arpeggios"], speech=-0.15),
    "acoustic": dict(p=(0.40, 0.4, 0.65, 0.50, 0.20), tex=["acoustic", "organic"], inst=["acoustic_guitar", "piano", "light_percussion", "strings"], speech=0.05),
}
MOODS: dict[str, tuple] = {  # energy, brightness, saturation, warmth
    "calm": (0.15, 0.6, 0.35, 0.0), "mysterious": (0.30, 0.2, 0.30, -0.05), "melancholic": (0.25, 0.3, 0.30, -0.05),
    "hopeful": (0.45, 0.7, 0.55, 0.10), "warm": (0.35, 0.6, 0.55, 0.20), "curious": (0.40, 0.55, 0.50, 0.0),
    "confident": (0.60, 0.5, 0.50, 0.0), "premium": (0.40, 0.35, 0.30, 0.0), "futuristic": (0.55, 0.35, 0.55, -0.20),
    "tense": (0.65, 0.25, 0.35, -0.05), "exciting": (0.85, 0.6, 0.75, 0.05),
}
SYNONYMS: dict[str, set[str]] = {
    "cinematic": {"film", "epic", "trailer", "score", "soundtrack", "dramatic", "cinematic"},
    "technology": {"tech", "technology", "futuristic", "digital", "synth", "electronic", "innovation"},
    "corporate": {"corporate", "business", "presentation", "professional", "uplifting-corporate"},
    "energetic": {"energetic", "upbeat", "driving", "dynamic", "powerful", "high-energy"},
    "ambient": {"ambient", "atmospheric", "texture", "soundscape", "drone"},
    "minimal": {"minimal", "minimalist", "sparse", "understated"},
    "premium": {"premium", "luxury", "elegant", "sophisticated", "high-end"},
    "luxury": {"premium", "luxury", "elegant", "sophisticated", "high-end"},
    "calm": {"calm", "relaxed", "peaceful", "soft", "chill"},
    "tense": {"tense", "suspense", "suspenseful", "dark", "thriller"},
    "suspenseful": {"tense", "suspense", "suspenseful", "dark", "thriller"},
    "exciting": {"exciting", "energetic", "upbeat", "action"},
    "hopeful": {"hopeful", "inspiring", "uplifting", "optimistic"},
    "emotional": {"emotional", "touching", "sentimental", "heartfelt"},
    "electronic": {"electronic", "edm", "synth", "techno", "house"},
    "orchestral": {"orchestral", "strings", "symphonic", "classical"},
    "acoustic": {"acoustic", "guitar", "folk", "organic"},
    "documentary": {"documentary", "underscore", "neutral"},
    "playful": {"playful", "fun", "quirky", "cheerful"},
    "futuristic": {"futuristic", "sci-fi", "scifi", "future", "synthwave"},
    "mysterious": {"mysterious", "enigmatic", "dark", "eerie"},
    "confident": {"confident", "bold", "powerful", "assured"},
    "curious": {"curious", "light", "investigative", "wonder"},
    "warm": {"warm", "friendly", "cozy"},
    "melancholic": {"melancholic", "sad", "wistful", "somber"},
}


def expand(term: str) -> set[str]:
    t = term.lower().replace("_", "-")
    return SYNONYMS.get(t, set()) | {t}


@dataclass
class Features:
    energy: float
    pace: float
    brightness: float
    saturation: float
    warmth: float
    speech_ratio: float


def _dist(vec, proto, w) -> float:
    return sum(wi * (a - b) ** 2 for wi, a, b in zip(w, vec, proto, strict=True)) ** 0.5


def rank_styles(f: Features, preset_styles: list[str], avoid: list[str]) -> list[tuple[str, float, list[str]]]:
    """Return [(style, score 0..1, evidence)] best first."""
    vec = (f.energy, f.pace, f.brightness, f.saturation, f.warmth)
    w = (2.0, 1.0, 1.0, 0.6, 0.6)
    out = []
    for name, s in STYLES.items():
        if name in avoid:
            continue
        d = _dist(vec, s["p"], w)
        score = max(0.0, 1.0 - d / 1.8)
        ev = [f"energy {f.energy:.2f} vs {name} prototype {s['p'][0]:.2f}"]
        if f.speech_ratio > 0.2:
            adj = s["speech"] * min(f.speech_ratio / 0.5, 1.0)
            score += adj
            if abs(adj) >= 0.03:
                ev.append(f"voiceover covers {f.speech_ratio:.0%} of the video ({'favours' if adj > 0 else 'penalises'} {name})")
        if name in preset_styles:
            score += 0.12
            ev.append("matches the platform/profile preset")
        out.append((name, score, ev))
    out.sort(key=lambda x: -x[1])
    return out


def rank_moods(f: Features, avoid: list[str]) -> list[tuple[str, float]]:
    vec = (f.energy, f.brightness, f.saturation, f.warmth)
    w = (2.0, 1.0, 0.6, 0.6)
    out = [(m, 1.0 - _dist(vec, p, w) / 1.8) for m, p in MOODS.items() if m not in avoid]
    out.sort(key=lambda x: -x[1])
    return out
