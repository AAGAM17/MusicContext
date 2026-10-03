"""Filesystem sandbox helpers.

`PathPolicy(roots=None)` is the trusted-CLI policy (the user is the principal).
With roots (MCP/REST), every input and output must resolve inside a root.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ..errors import InvalidPathError, PermissionDeniedError


@dataclass
class PathPolicy:
    roots: list[Path] | None = None

    def _check_inside(self, p: Path, what: str) -> None:
        if self.roots is None:
            return
        for r in self.roots:
            try:
                p.relative_to(r)
                return
            except ValueError:
                continue
        raise PermissionDeniedError(
            f"{what} '{p.name}' is outside the allowed directories.",
            "Allowed roots: " + ", ".join(str(r) for r in self.roots) + ". Add more with MUSICCONTEXT_ROOTS=dir1:dir2.",
        )

    def input_file(self, path: str | os.PathLike, what: str = "input") -> Path:
        s = os.fspath(path)
        if "\x00" in s:
            raise InvalidPathError("Path contains a NUL byte.")
        p = Path(s).expanduser()
        try:
            rp = p.resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise InvalidPathError(f"{what} file not found: {p}", "Check the path; relative paths resolve from the current directory.") from None
        self._check_inside(rp, what)
        mode = rp.stat().st_mode
        if not stat.S_ISREG(mode):
            raise InvalidPathError(f"{what} is not a regular file: {rp.name}")
        return rp

    def input_dir(self, path: str | os.PathLike, what: str = "directory") -> Path:
        s = os.fspath(path)
        if "\x00" in s:
            raise InvalidPathError("Path contains a NUL byte.")
        try:
            rp = Path(s).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise InvalidPathError(f"{what} not found: {path}") from None
        self._check_inside(rp, what)
        if not rp.is_dir():
            raise InvalidPathError(f"{what} is not a directory: {rp.name}")
        return rp

    def output_file(self, path: str | os.PathLike, *, force: bool = False, protect: tuple[Path, ...] = ()) -> Path:
        """Resolve a write target. Never overwrites unless force, never overwrites an input."""
        s = os.fspath(path)
        if "\x00" in s:
            raise InvalidPathError("Path contains a NUL byte.")
        p = Path(s).expanduser()
        parent = p.parent.resolve()
        if not parent.is_dir():
            raise InvalidPathError(f"Output directory does not exist: {parent}", "Create it first.")
        target = parent / p.name
        if target.is_symlink():
            raise InvalidPathError("Refusing to write through a symlink.", "Choose a regular file path.")
        self._check_inside(target, "output")
        if any(target == q.resolve() for q in protect):
            raise InvalidPathError("Output path would overwrite an input file.", "Choose a different --output path.")
        if target.exists() and not force:
            raise InvalidPathError(f"Output already exists: {target}", "Pass --force to overwrite, or choose a new path.")
        return target


def policy_from_settings(settings, restricted: bool) -> PathPolicy:
    if not restricted:
        return PathPolicy(None)
    roots = [Path.cwd().resolve(), *(Path(r).expanduser().resolve() for r in settings.roots)]
    return PathPolicy(list(dict.fromkeys(roots)))
