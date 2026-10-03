"""Treat everything derived from media as untrusted data.

Text from OCR, subtitles, transcripts, container tags and provider responses is
sanitized and length-bounded before it enters any output. It is never executed,
never used to build commands, and flagged when it looks like an instruction.
"""

from __future__ import annotations

import re
import unicodedata

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts?)|disregard (the )?(system|previous)|"
    r"you are now|system prompt|reveal .{0,30}(api[_ -]?keys?|secrets?|passwords?|credentials)|"
    r"\brm\s+-rf\b|\bcurl\b.{0,40}\|\s*(ba)?sh|upload this file to|exfiltrate|send .{0,40} to https?://|"
    r"(api[_-]?key|token|secret)\s*[=:]\s*\S+|run (the following|this) command)",
    re.I,
)

MAX_FIELD = 200


def sanitize_text(value: object, max_len: int = MAX_FIELD) -> str:
    s = "" if value is None else str(value)
    s = _ANSI.sub("", s)
    s = unicodedata.normalize("NFKC", s)
    s = _CTRL.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    from .secrets import redact

    s = redact(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def looks_like_instruction(value: str) -> bool:
    return bool(_INJECTION.search(value or ""))


def sanitize_tags(tags, max_tags: int = 32) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        s = sanitize_text(t, 48).lower()
        if s and s not in out:
            out.append(s)
        if len(out) >= max_tags:
            break
    return out


def sanitize_meta(d: dict, max_items: int = 32) -> dict[str, str]:
    """Flatten container/provider metadata to bounded, sanitized strings."""
    out = {}
    for k, v in list(d.items())[:max_items]:
        if isinstance(v, (dict, list)):
            continue
        out[sanitize_text(k, 48)] = sanitize_text(v)
    return out


def injection_warnings(fields: dict[str, str]) -> list[str]:
    return [
        f"Text in '{k}' looks like an instruction; it was treated as plain data and not acted on."
        for k, v in fields.items()
        if looks_like_instruction(v)
    ]


def bound_json(obj, max_chars: int = 60_000, max_list: int = 200):
    """Bound a JSON-like value: cap list lengths first, then total size."""
    import json

    def cap(o, depth=0):
        if isinstance(o, list):
            if len(o) > max_list:
                return [cap(x, depth + 1) for x in o[:max_list]] + [{"_truncated": f"{len(o) - max_list} more items"}]
            return [cap(x, depth + 1) for x in o]
        if isinstance(o, dict):
            return {k: cap(v, depth + 1) for k, v in o.items()}
        return o

    capped = cap(obj)
    n = len(json.dumps(capped, default=str))
    if n <= max_chars:
        return capped
    ml = max_list
    while ml > 5 and n > max_chars:
        ml //= 2

        def cap2(o, ml=ml):  # ml bound per iteration on purpose
            if isinstance(o, list):
                head = [cap2(x) for x in o[:ml]]
                return head + [{"_truncated": f"{len(o) - ml} more items"}] if len(o) > ml else head
            if isinstance(o, dict):
                return {k: cap2(v) for k, v in o.items()}
            return o

        capped = cap2(obj)
        n = len(json.dumps(capped, default=str))
    if n > max_chars and isinstance(capped, dict):
        capped["_truncated_output"] = "Output exceeded the size bound; request a narrower section or use --output."
    return capped
