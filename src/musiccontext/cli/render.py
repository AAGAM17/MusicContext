"""Human-readable terminal output. Agents use --json; this is for people."""

from __future__ import annotations

import os
import shutil
import sys

BLOCKS = "▁▂▃▄▅▆▇█"


def _tty() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _tty() else text


def bold(t):
    return c(t, "1")


def dim(t):
    return c(t, "2")


def cyan(t):
    return c(t, "36")


def yellow(t):
    return c(t, "33")


def red(t):
    return c(t, "31")


def green(t):
    return c(t, "32")


def width(default: int = 80) -> int:
    return min(shutil.get_terminal_size((default, 24)).columns, 110)


def bar(value: float, cells: int = 18) -> str:
    """A 0-1 value as a block bar."""
    value = max(0.0, min(1.0, value))
    full = int(value * cells)
    rem = value * cells - full
    s = "█" * full + (BLOCKS[int(rem * 8)] if full < cells and rem > 0.05 else "")
    return s.ljust(cells, " ")


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    return "".join(BLOCKS[min(7, int((v - lo) / span * 7.99))] for v in values)


def heading(text: str) -> str:
    return "\n" + bold(text)


def kv(label: str, value: str, pad: int = 16) -> str:
    return f"  {dim(label.ljust(pad))} {value}"


def t(seconds: float) -> str:
    return f"{seconds:.1f}s"


def span(a: float, b: float) -> str:
    return f"{a:6.1f}-{b:<6.1f}"


def src_tag(source: str) -> str:
    colour = {"detected": green, "user": cyan, "estimated": yellow, "inferred": yellow, "provider": yellow}.get(source, dim)
    return colour(f"[{source}]")


def wrap(text: str, indent: str = "  ", w: int | None = None, hang: str | None = None) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, (w or width()) - len(indent), initial_indent=indent,
                                   subsequent_indent=hang if hang is not None else indent)) or indent.rstrip()


def bullets(items, indent="  - ", limit: int = 12) -> str:
    return "\n".join(wrap(str(i), indent=indent, hang=" " * len(indent)) for i in list(items)[:limit])


def level_colour(level: str) -> str:
    return {"high": green, "medium": yellow, "low": red, "unknown": dim, "not_applicable": dim}.get(level, dim)(level)
