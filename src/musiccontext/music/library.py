"""The local music library: a SQLite index of audio the user already owns.

This is why MusicContext works with zero cloud services. Every number stored here was
measured on this machine (tempo, key, loudness via `music.features`) or read from a
`.license.json` sidecar the user wrote; nothing is claimed on a provider's behalf.
Text pulled out of container tags is UNTRUSTED and sanitized before it is stored.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..analysis.media import content_hash, ffprobe
from ..errors import ArtifactError, MusicContextError, UnsupportedMediaError
from ..schemas import MusicCandidate, MusicFeatures, MusicLicense
from ..security.content import sanitize_meta, sanitize_tags, sanitize_text
from ..security.paths import PathPolicy
from ..security.secrets import redact
from .features import analyze_music
from .licensing import license_for_local_file

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma"}
)
SCHEMA_VERSION = 1
MAX_SCAN_FILES = 20_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    track_id       TEXT PRIMARY KEY,
    path           TEXT UNIQUE NOT NULL,
    content_hash   TEXT NOT NULL,
    title          TEXT,
    artist         TEXT,
    duration       REAL,
    bpm            REAL,
    bpm_confidence REAL,
    bpm_source     TEXT,
    music_key      TEXT,
    loudness_lufs  REAL,
    tags           TEXT,
    features       TEXT,
    license        TEXT,
    metadata       TEXT,
    mtime          REAL,
    size           INTEGER,
    added_at       TEXT,
    analyzed_at    TEXT
);
CREATE INDEX IF NOT EXISTS tracks_content_hash ON tracks(content_hash);
CREATE INDEX IF NOT EXISTS tracks_bpm ON tracks(bpm);
CREATE INDEX IF NOT EXISTS tracks_duration ON tracks(duration);
"""

_UPSERT = (
    "INSERT OR REPLACE INTO tracks (track_id, path, content_hash, title, artist, duration, bpm, "
    "bpm_confidence, bpm_source, music_key, loudness_lufs, tags, features, license, metadata, mtime, "
    "size, added_at, analyzed_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


@dataclass
class ScanResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{len(self.added)} added, {len(self.updated)} updated, {len(self.unchanged)} unchanged, "
                f"{len(self.duplicates)} duplicate, {len(self.errors)} failed")


# ---------------------------------------------------------------------------- database


def open_db(settings) -> sqlite3.Connection:
    """Open (and create if needed) the library index, refusing a format this build cannot read."""
    db = Path(settings.library_db)
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db, detect_types=0)
    except (OSError, sqlite3.Error) as e:
        raise ArtifactError(
            f"Cannot open the music library index at {db}: {type(e).__name__}.",
            "Check that MUSICCONTEXT_HOME is writable, or point it somewhere else.",
        ) from e
    conn.row_factory = sqlite3.Row
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found == 0:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    elif found != SCHEMA_VERSION:
        conn.close()
        raise ArtifactError(
            f"The music library index at {db} uses schema version {found}, but this build reads version "
            f"{SCHEMA_VERSION}.",
            "A newer MusicContext wrote it: upgrade, or delete the file and re-run "
            "`musiccontext library scan <dir>` to rebuild the index (your audio files are untouched).",
        )
    return conn


def candidate_from_row(row: sqlite3.Row) -> MusicCandidate:
    """Rebuild a candidate from a stored row. Partial or older JSON degrades to None, never raises."""
    return MusicCandidate(
        id=row["track_id"],
        provider="local",
        title=row["title"] or Path(row["path"]).stem,
        artist=row["artist"],
        uri=row["path"],
        tags=_json_list(row["tags"]),
        features=_model_or_none(MusicFeatures, row["features"]),
        license=_model_or_none(MusicLicense, row["license"]),
        metadata=_json_dict(row["metadata"]),
    )


def _model_or_none(model, blob):
    if not blob:
        return None
    try:
        return model.model_validate(json.loads(blob))
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        return None  # a row written by another schema is still listable; it just reports less


def _json_list(blob) -> list[str]:
    try:
        v = json.loads(blob) if blob else []
    except json.JSONDecodeError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _json_dict(blob) -> dict[str, str]:
    try:
        v = json.loads(blob) if blob else {}
    except json.JSONDecodeError:
        return {}
    return {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {}


# ---------------------------------------------------------------------------- indexing


def add_track(
    settings,
    path: Path,
    *,
    tags: Iterable[str] = (),
    force: bool = False,
    analyze: bool = True,
) -> MusicCandidate:
    """Index one audio file and return what the library now knows about it."""
    conn = open_db(settings)
    try:
        candidate, _ = _index(conn, settings, Path(path), tags, force, analyze)
        return candidate
    finally:
        conn.close()


def _index(
    conn: sqlite3.Connection, settings, path: Path, tags: Iterable[str], force: bool, analyze: bool
) -> tuple[MusicCandidate, str]:
    """The worker behind add_track/scan_directory. Returns (candidate, added|updated|unchanged|duplicate)."""
    p = PathPolicy(None).input_file(path, "audio")
    st = p.stat()
    row = conn.execute("SELECT * FROM tracks WHERE path = ?", (str(p),)).fetchone()
    if row is not None and not force and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
        return candidate_from_row(row), "unchanged"

    digest = content_hash(p)
    track_id = digest[:16]
    twin = conn.execute(
        "SELECT * FROM tracks WHERE content_hash = ? AND path <> ?", (digest, str(p))
    ).fetchone()
    if twin is not None:
        if Path(twin["path"]).exists():
            return candidate_from_row(twin), "duplicate"
        conn.execute("DELETE FROM tracks WHERE path = ?", (twin["path"],))  # the copy we indexed is gone
    conn.execute("DELETE FROM tracks WHERE path = ? AND track_id <> ?", (str(p), track_id))  # edited in place

    duration, file_tags = _probe(p, settings)
    lowered = {k.lower(): v for k, v in file_tags.items()}
    features = analyze_music(p, settings) if analyze else MusicFeatures(duration=round(duration, 3))
    lic = license_for_local_file(p, file_tags)
    tag_list = sanitize_tags([*tags, *_split_tag(lowered.get("genre"))])
    meta = {**lowered, "content_hash": digest, "indexed_by": "local library scan"}
    now = datetime.now(UTC).isoformat(timespec="seconds")

    conn.execute(_UPSERT, (
        track_id, str(p), digest,
        sanitize_text(lowered.get("title")) or p.stem,
        sanitize_text(lowered.get("artist") or lowered.get("album_artist")) or None,
        round(features.duration, 3),
        features.bpm.value if features.bpm else None,
        features.bpm.confidence if features.bpm else None,
        features.bpm.source if features.bpm else None,
        features.key, features.loudness_lufs,
        json.dumps(tag_list), features.model_dump_json(), lic.model_dump_json(),
        json.dumps(sanitize_meta(meta)),
        st.st_mtime, st.st_size,
        (row["added_at"] if row is not None else now), (now if analyze else None),
    ))
    conn.commit()
    stored = conn.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,)).fetchone()
    return candidate_from_row(stored), ("updated" if row is not None else "added")


def _probe(path: Path, settings) -> tuple[float, dict[str, str]]:
    """Duration plus the container/stream tags, sanitized. ffprobe is the only thing that reads the file."""
    info = ffprobe(path, settings)
    fmt = info.get("format") or {}
    streams = [s for s in (info.get("streams") or []) if s.get("codec_type") == "audio"]
    if not streams:
        raise UnsupportedMediaError(
            f"'{path.name}' has no audio stream.",
            "The library indexes audio only; pass a music file, not a video or an image.",
        )
    duration = _float(fmt.get("duration")) or _float(streams[0].get("duration"))
    if not duration or duration <= 0:
        raise UnsupportedMediaError(
            f"ffprobe reported no playable duration for '{path.name}'.",
            "The file may be truncated; try re-encoding it with `ffmpeg -i <file> out.wav`.",
        )
    raw = {**(fmt.get("tags") or {}), **(streams[0].get("tags") or {})}
    return duration, sanitize_meta(raw)


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _split_tag(value: str | None) -> list[str]:
    return [s for s in (value or "").replace("/", ",").replace(";", ",").split(",") if s.strip()]


def scan_directory(
    settings,
    directory: Path,
    *,
    recursive: bool = True,
    force: bool = False,
    progress: Callable[[Path], None] | None = None,
) -> ScanResult:
    """Index every audio file under `directory`. One bad file never aborts the scan."""
    root = PathPolicy(None).input_dir(directory, "library directory")
    res = ScanResult()
    # ponytail: the tree is listed and sorted up front so the scan order (and the index) is
    # deterministic; fine for a personal library, revisit if someone points this at a NAS.
    files: list[Path] = []
    for p in sorted(root.rglob("*") if recursive else root.iterdir()):
        if p.suffix.lower() not in AUDIO_EXTENSIONS or not p.is_file():
            continue
        if p.is_symlink() and not _inside(p.resolve(), root):
            res.errors.append((str(p), "symlink resolves outside the scanned directory; skipped"))
            continue
        files.append(p)
        if len(files) >= MAX_SCAN_FILES:
            res.errors.append((str(root), f"stopped after {MAX_SCAN_FILES} audio files; the rest of this "
                                          "directory was not indexed. Scan subdirectories separately."))
            break

    buckets = {"added": res.added, "updated": res.updated, "unchanged": res.unchanged, "duplicate": res.duplicates}
    conn = open_db(settings)
    try:
        for p in files:
            if progress is not None:
                progress(p)
            try:
                _, status = _index(conn, settings, p, (), force, True)
            except MusicContextError as e:
                res.errors.append((str(p), redact(f"{e.message} {e.hint or ''}".strip())))
            except (OSError, ValueError, sqlite3.Error) as e:
                res.errors.append((str(p), redact(f"{type(e).__name__}: {e}")))
            else:
                buckets[status].append(str(p))
    finally:
        conn.close()
    return res


def _inside(resolved: Path, root: Path) -> bool:
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------- reading


def list_tracks(settings, limit: int = 100, offset: int = 0) -> list[MusicCandidate]:
    conn = open_db(settings)
    try:
        rows = conn.execute(
            "SELECT * FROM tracks ORDER BY added_at, track_id LIMIT ? OFFSET ?",
            (max(0, int(limit)), max(0, int(offset))),
        ).fetchall()
    finally:
        conn.close()
    return [candidate_from_row(r) for r in rows]


def get_track(settings, track_id: str) -> MusicCandidate | None:
    conn = open_db(settings)
    try:
        row = conn.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,)).fetchone()
    finally:
        conn.close()
    return candidate_from_row(row) if row is not None else None


def remove_track(settings, track_id: str) -> bool:
    """Forget a track. The audio file itself is never touched."""
    conn = open_db(settings)
    try:
        cur = conn.execute("DELETE FROM tracks WHERE track_id = ?", (track_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def library_stats(settings) -> dict:
    conn = open_db(settings)
    try:
        count, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(duration), 0) FROM tracks").fetchone()
        with_bpm = conn.execute("SELECT COUNT(*) FROM tracks WHERE bpm IS NOT NULL").fetchone()[0]
        rows = conn.execute("SELECT tags, license FROM tracks ORDER BY track_id").fetchall()
    finally:
        conn.close()
    hist: Counter[str] = Counter()
    verified = 0
    for r in rows:
        hist.update(_json_list(r["tags"]))
        lic = _model_or_none(MusicLicense, r["license"])
        if lic is not None and lic.verified:
            verified += 1
    return {
        "count": int(count),
        "total_duration": round(float(total), 3),
        "with_bpm": int(with_bpm),
        "with_verified_license": verified,
        "top_tags": dict(hist.most_common(15)),
        "database": str(settings.library_db),
    }


# ---------------------------------------------------------------------------- search


def search_tracks(settings, query, limit: int = 20) -> list[MusicCandidate]:
    """Hard-filter the index, then score what survives. Best first, deterministically."""
    conn = open_db(settings)
    try:
        rows = conn.execute("SELECT * FROM tracks ORDER BY track_id").fetchall()
    finally:
        conn.close()
    # ponytail: whole-table scan plus Python scoring - exact, readable, and fast enough for a
    # personal library. Push the filters into SQL (and the text part into FTS5) past ~10k tracks.
    terms = _terms(query)
    scored = [(_score(c, query, terms), c) for c in map(candidate_from_row, rows) if _passes(c, query)]
    scored.sort(key=lambda sc: (-sc[0], sc[1].id))
    return [c for _, c in scored[: max(0, int(limit))]]


def _tempos(features: MusicFeatures | None) -> list[float]:
    """Every tempo a track can honestly answer to. Half and double time are the same groove, and the
    stored alternatives are clamped to 55-200 BPM, so derive the ratios too (120 answers a 240 brief)."""
    if features is None or features.bpm is None or features.bpm.value <= 0:
        return []
    b = features.bpm.value
    return [b, b * 2.0, b / 2.0, *features.bpm_alternatives]


def _haystack(c: MusicCandidate) -> str:
    return " ".join([*c.tags, c.title or "", c.artist or ""]).lower()


def _terms(query) -> list[str]:
    raw = [*query.tags_any, *query.styles, *query.moods, *(query.text or "").split()]
    return list(dict.fromkeys(t.strip().lower() for t in raw if t and t.strip()))


def _passes(c: MusicCandidate, query) -> bool:
    f = c.features
    if query.bpm_range:
        lo, hi = query.bpm_range
        if not any(lo <= t <= hi for t in _tempos(f)):
            return False  # no measured tempo is not a match: the library never guesses one
    if query.min_duration and (f is None or f.duration < query.min_duration):
        return False
    if query.tags_none:
        hay = _haystack(c)
        if any(t.strip().lower() in hay for t in query.tags_none if t and t.strip()):
            return False
    if query.vocals == "none" and f is not None and f.has_vocals:
        return False
    return True


def _score(c: MusicCandidate, query, terms: Sequence[str]) -> float:
    """Tag/title overlap (x3), tempo closeness (x2), duration fit (x1). Rounded so ties break by id."""
    total = 0.0
    if terms:
        hay = _haystack(c)
        total += 3.0 * sum(1 for t in terms if t in hay) / len(terms)
    tempos = _tempos(c.features)
    if query.target_bpm and tempos:
        off = min(abs(t - query.target_bpm) for t in tempos)
        total += 2.0 * max(0.0, 1.0 - off / 30.0)
    wanted = query.duration or query.min_duration
    duration = c.features.duration if c.features else 0.0
    if wanted and duration:
        total += 1.0 if duration >= wanted else duration / wanted
    return round(total, 6)
