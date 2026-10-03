"""Thin, safe wrappers around ffmpeg/ffprobe. Always argv lists, never a shell."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from ..errors import CorruptMediaError, FFmpegMissingError, UnsupportedMediaError


def which(tool: str, settings=None) -> str:
    name = getattr(settings, tool, tool) if settings else tool
    p = shutil.which(name)
    if not p:
        raise FFmpegMissingError(tool)
    return p


def run(argv: list[str], *, timeout: float = 3600, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(argv, capture_output=True, timeout=timeout, text=True, errors="replace")
    except subprocess.TimeoutExpired:
        raise CorruptMediaError(f"{Path(argv[0]).name} timed out after {timeout:.0f}s.", "The media may be corrupt or extremely large.") from None
    if check and cp.returncode != 0:
        raise CorruptMediaError(f"{Path(argv[0]).name} failed: {cp.stderr.strip().splitlines()[-1] if cp.stderr.strip() else 'unknown error'}")
    return cp


def content_hash(path: Path) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def ffprobe(path: Path, settings=None) -> dict:
    exe = which("ffprobe", settings)
    cp = run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", str(path)], timeout=120, check=False)
    if cp.returncode != 0:
        raise UnsupportedMediaError(f"Cannot read '{path.name}' as media: {cp.stderr.strip().splitlines()[-1] if cp.stderr.strip() else 'unknown format'}",
                                    "Supported inputs are anything FFmpeg can decode (mp4, mov, mkv, webm, wav, mp3, flac, ...).")
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        raise CorruptMediaError(f"ffprobe returned unreadable output for '{path.name}'.") from None


def decode_mono(path: Path, sr: int, settings=None, chunk_seconds: float = 30.0, max_seconds: float | None = None) -> Iterator[np.ndarray]:
    """Stream mono float32 PCM in chunks (bounded memory)."""
    exe = which("ffmpeg", settings)
    argv = [exe, "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr)]
    if max_seconds:
        argv += ["-t", str(max_seconds)]
    argv += ["-f", "f32le", "-"]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)  # noqa: S603
    assert proc.stdout is not None
    n = int(sr * chunk_seconds) * 4
    got_any = False
    try:
        while buf := proc.stdout.read(n):
            buf = buf[: len(buf) - (len(buf) % 4)]
            if buf:
                got_any = True
                yield np.frombuffer(buf, dtype="<f4")
    finally:
        proc.stdout.close()
        proc.kill() if proc.poll() is None else None
        proc.wait()
    if not got_any:
        return
