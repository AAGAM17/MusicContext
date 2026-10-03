"""Content-addressed cache. Keys bind content hash + tool/analysis versions + config + step.

An entry also stores its own key material; on read it is re-verified, so an artifact written by
an incompatible version can never be returned as current.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .. import ANALYSIS_VERSION, __version__


def make_key(step: str, content_hash: str, config: dict | None = None, extra: dict | None = None) -> tuple[str, dict]:
    material = {
        "step": step, "content": content_hash, "tool_version": __version__, "analysis_version": ANALYSIS_VERSION,
        "config": config or {}, "extra": extra or {},
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
    return digest, material


class Cache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = root
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str, material: dict) -> dict | None:
        if not self.enabled:
            return None
        p = self._path(key)
        try:
            entry = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if entry.get("material") != json.loads(json.dumps(material, sort_keys=True, default=str)):
            return None  # stale or colliding entry: never served
        return entry.get("value")

    def put(self, key: str, material: dict, value: dict) -> None:
        if not self.enabled:
            return
        p = self._path(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump({"material": json.loads(json.dumps(material, sort_keys=True, default=str)), "value": value}, f)
            os.replace(tmp, p)
        except OSError:
            pass  # a read-only cache must never break analysis

    def clear(self) -> int:
        n = 0
        for f in self.root.glob("*/*.json"):
            f.unlink(missing_ok=True)
            n += 1
        return n
