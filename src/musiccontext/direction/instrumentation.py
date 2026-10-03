from __future__ import annotations

from ..schemas import Instrumentation
from .mood import STYLES

VO_FRIENDLY = ["soft_pads", "plucks", "subtle_percussion", "sub_bass"]
VO_AVOID = ["vocals", "lead_vocal", "busy_lead_melody", "distorted_guitar"]


def choose_instrumentation(styles: list[str], user: list[str], brand: list[str], avoid: list[str], speech_ratio: float) -> Instrumentation:
    style_inst: list[str] = []
    for s in styles:
        for i in STYLES.get(s, {}).get("inst", []):
            if i not in style_inst:
                style_inst.append(i)
    preferred = list(dict.fromkeys([*user, *brand, *style_inst[:3]]))
    optional = [i for i in style_inst if i not in preferred]
    prohibited = list(dict.fromkeys(avoid))
    if speech_ratio >= 0.15:
        for i in VO_AVOID:
            if i not in prohibited:
                prohibited.append(i)
        for i in VO_FRIENDLY:
            if i not in preferred and i not in prohibited:
                optional.append(i)
    preferred = [i for i in preferred if i not in prohibited]
    optional = [i for i in dict.fromkeys(optional) if i not in prohibited and i not in preferred]
    return Instrumentation(preferred=preferred, optional=optional, prohibited=prohibited)
