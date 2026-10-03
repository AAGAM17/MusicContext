"""Secret redaction for logs, errors, config display and tool output."""

from __future__ import annotations

import logging
import os
import re

REDACTED = "***redacted***"
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
_PATTERNS = [
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-~+/]{12,}=*", re.I),
    re.compile(r"(?i)\b([a-z0-9_.\-]*(?:api[_-]?key|token|secret|passw(?:or)?d|credential)[a-z0-9_.\-]*)(\s*[=:]\s*)(['\"]?)[^\s'\",;]+"),
]


def _env_secret_values() -> list[str]:
    return [v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v) >= 8]


def redact(text: str) -> str:
    if not text:
        return text
    for v in _env_secret_values():
        text = text.replace(v, REDACTED)
    text = _PATTERNS[0].sub(REDACTED, text)
    for p in _PATTERNS[1:4]:
        text = p.sub(REDACTED, text)
    return _PATTERNS[4].sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)


def redact_obj(obj):
    """Recursively redact strings inside JSON-like data."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: (REDACTED if isinstance(k, str) and _SECRET_NAME.search(k) and isinstance(v, str) and v else redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True
