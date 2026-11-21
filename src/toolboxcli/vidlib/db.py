"""vidlib's library database: a single SQLite file in the library root.

`.vidlib.db` replaces the old `.optimiselib.json` log. It needs no server (Python's
built-in sqlite3), and unlike the log it identifies a video by *what it is* — its set of
people, title and collection (core.parse_name) — rather than by its exact filename, so
reordered people or a hand rename don't make a known video look new, and duplicates are
found across the whole library rather than in one folder.

Tables:
    videos        one row per file, keyed by its root-relative POSIX path
    people        each distinct person, by normalised name
    video_people  which people appear in which video
    video_tags    each [tag] on each video, for searching by tag
    events        what vidlib did (encoded, replaced, parked, migrated, ...)

The name-derived columns (people, title, collection, tags, the match key) are always
recomputed from the path on write, so they can never drift from the filename.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path, PurePosixPath

from toolboxcli.vidlib import core

DB_NAME = ".vidlib.db"
# What the database was called before optimiselib became vidlib; adopted on first open.
LEGACY_DB_NAME = ".optimiselib.db"
# SQLite's WAL-mode companions, which belong to the database file and move with it.
_DB_SIDECARS = ("", "-wal", "-shm")
JSON_LOG_NAME = ".optimiselib.json"
MIGRATED_SUFFIX = ".migrated"
REVIEW_DIR = "_review"
# Where downloads are written until they finish; the library scan never looks inside, so a
# half-downloaded file is never recorded (a file in any other subfolder counts as sorted).
INCOMING_DIR = "_incoming"

# 1: initial. 2: names that don't follow People - Title - Collection are no longer split
# into fields (core.VideoName.complete); upgrading re-derives every row and drops the
# people that only those mis-split names had created. 3: the downloads table. 4: sets of
# videos the user said aren't parts of one (part_dismissals).
SCHEMA_VERSION = 4

# Statuses a row can have. "compressed" is a known, already-compressed file that hasn't been
# sorted yet (the old log's "reconciled"); "missing" is a row whose file has disappeared.
STATUSES = ("sorted", "compressed", "awaiting_trip", "skipped", "review", "failed", "missing")

_SCHEMA = """
CREATE TABLE videos (
    id             INTEGER PRIMARY KEY,
    path           TEXT    NOT NULL UNIQUE,   -- root-relative, '/'-separated
    people_key     TEXT    NOT NULL,          -- core.people_key
    title          TEXT    NOT NULL,
    title_key      TEXT    NOT NULL,
    collection     TEXT    NOT NULL,
    collection_key TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    height         INTEGER NOT NULL DEFAULT 0,
    resolution     TEXT    NOT NULL DEFAULT '',  -- core.resolution_label(height)
    bit_depth      INTEGER NOT NULL DEFAULT 8,
    has_sub        INTEGER NOT NULL DEFAULT 0,
    duration       REAL    NOT NULL DEFAULT 0,
    size           INTEGER NOT NULL DEFAULT 0,
    mtime          REAL    NOT NULL DEFAULT 0,
    source_height  INTEGER NOT NULL DEFAULT 0,  -- before encoding, for duplicate verdicts
    original_size  INTEGER NOT NULL DEFAULT 0,
    backend        TEXT    NOT NULL DEFAULT '',
    failures       INTEGER NOT NULL DEFAULT 0,
    reason         TEXT    NOT NULL DEFAULT '',
    compared_with  TEXT    NOT NULL DEFAULT '',
    replaced       TEXT    NOT NULL DEFAULT '',
    updated_at     TEXT    NOT NULL
);
CREATE INDEX videos_match ON videos (people_key, title_key, collection_key);
CREATE INDEX videos_file ON videos (size, mtime);

CREATE TABLE people (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL,          -- as first written
    key  TEXT NOT NULL UNIQUE    -- core.normalize(name)
);

CREATE TABLE video_people (
    video_id  INTEGER NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people (id),
    PRIMARY KEY (video_id, person_id)
);

CREATE TABLE video_tags (
    video_id INTEGER NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    tag_key  TEXT NOT NULL,
    PRIMARY KEY (video_id, tag_key)
);

CREATE TABLE events (
    id     INTEGER PRIMARY KEY,
    at     TEXT NOT NULL,
    path   TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""

# Added in schema 3. Kept apart from _SCHEMA (the v1 tables) because a new database is
# built by running every migration step in order, so it ends up exactly like an upgraded one.
_DOWNLOADS_SCHEMA = """
CREATE TABLE downloads (
    id           INTEGER PRIMARY KEY,
    url          TEXT NOT NULL,
    name         TEXT NOT NULL,              -- filename stem; '' = the video's own title
    page_title   TEXT NOT NULL DEFAULT '',   -- the pasted or fetched title it was named from
    status       TEXT NOT NULL,              -- DOWNLOAD_STATUSES
    error        TEXT NOT NULL DEFAULT '',
    file         TEXT NOT NULL DEFAULT '',   -- absolute path while it waits outside the library
    library_path TEXT NOT NULL DEFAULT '',   -- root-relative path it was imported at
    added_at     TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

# Added in schema 4: a set of video ids the user said aren't parts of one video, so the
# Parts view stops offering it (until another video joins the set).
_DISMISSALS_SCHEMA = """
CREATE TABLE part_dismissals (
    id        INTEGER PRIMARY KEY,
    video_ids TEXT NOT NULL,             -- JSON list of videos.id, sorted
    at        TEXT NOT NULL
);
"""

# naming: waiting for a name (bulk paste); queued → downloading → downloaded, which in
# download-only mode waits in its folder until it's moved into the library: imported.
DOWNLOAD_STATUSES = ("naming", "queued", "downloading", "downloaded", "imported", "failed",
                     "cancelled")


@dataclass
class Download:
    url: str
    name: str = ""
    page_title: str = ""
    status: str = "queued"
    error: str = ""
    file: str = ""
    library_path: str = ""
    added_at: str = ""
    updated_at: str = ""
    id: int | None = None


_DOWNLOAD_COLUMNS = [f.name for f in fields(Download) if f.name != "id"]


def adopt_legacy_db(root: Path) -> bool:
    """Rename `.optimiselib.db` (and its -wal/-shm files) to `.vidlib.db`. True if it did.

    Only when no `.vidlib.db` exists yet: with both present the old file is left alone (see
    legacy_db_conflict), never merged or overwritten."""
    if (root / DB_NAME).exists() or not (root / LEGACY_DB_NAME).is_file():
        return False
    for suffix in _DB_SIDECARS:
        old = root / f"{LEGACY_DB_NAME}{suffix}"
        if old.exists():
            old.rename(root / f"{DB_NAME}{suffix}")
    return True


def legacy_db_conflict(root: Path) -> bool:
    """Both `.optimiselib.db` and `.vidlib.db` exist, so the old one was never adopted."""
    return (root / DB_NAME).is_file() and (root / LEGACY_DB_NAME).is_file()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Video:
    """One file the library knows about. Name-derived fields come from `path`."""

    path: str
    status: str = "sorted"
    height: int = 0
    bit_depth: int = 8
    has_sub: bool = False
    duration: float = 0.0
    size: int = 0
    mtime: float = 0.0
    source_height: int = 0
    original_size: int = 0
    backend: str = ""
    failures: int = 0
    reason: str = ""
    compared_with: str = ""
    replaced: str = ""
    updated_at: str = ""

    @property
    def name(self) -> core.VideoName:
        return core.parse_name(PurePosixPath(self.path).stem)

    @property
    def filename(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def resolution(self) -> str:
        return core.resolution_label(self.height)


_VIDEO_COLUMNS = [f.name for f in fields(Video)]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _resolution_query(text: str) -> str:
    """"720", "720p" and "720P" all mean the 720p rung."""
    text = text.strip().lower()
    return f"{text}p" if text.isdigit() else text


class LibraryDB:
    """An open `.vidlib.db`. Use as a context manager, or call close()."""

    def __init__(self, path: Path, in_memory: bool = False, shared: bool = False) -> None:
        """With *shared*, the connection may be used from any thread; the caller then
        serialises access itself (the web server's download manager holds a lock)."""
        self.path = path
        if in_memory:
            # A private copy for a dry run: everything it writes (a JSON import, a library
            # sync) happens in memory and vanishes, while reads see the real data.
            self.conn = sqlite3.connect(":memory:")
            if path.is_file():
                with sqlite3.connect(path, timeout=10) as source:
                    source.backup(self.conn)
                source.close()
        else:
            self.conn = sqlite3.connect(path, timeout=10, check_same_thread=not shared)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets a report or query read while the watcher writes. Fine on a local disk;
        # SQLite's locking isn't reliable on network shares, where the library never lives.
        self.conn.execute("PRAGMA journal_mode = WAL")
        try:
            self._migrate_schema()
        except BaseException:
            self.conn.close()  # don't leave the file locked (Windows) when refusing it
            raise

    @classmethod
    def open(cls, root: Path, in_memory: bool = False, shared: bool = False) -> LibraryDB:
        """The library's database; with in_memory, a throwaway copy of it (for -n).

        A library last used as optimiselib has `.optimiselib.db`; it's renamed `.vidlib.db`
        here (a dry run reads it where it is instead, renaming nothing)."""
        path = root / DB_NAME
        if in_memory:
            legacy = root / LEGACY_DB_NAME
            if not path.exists() and legacy.is_file():
                path = legacy
        else:
            adopt_legacy_db(root)
        return cls(path, in_memory=in_memory, shared=shared)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> LibraryDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate_schema(self) -> None:
        version: int = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with self.conn:
                self.conn.executescript(_SCHEMA)
                self.conn.execute("PRAGMA user_version = 2")
            version = 2
        if version < 2:
            # Same tables; only the name-derived columns change meaning. Re-writing each
            # row recomputes them from its path under the current rules.
            for video in self.all(include_missing=True):
                self.upsert(video)
            self.prune_people()
            with self.conn:
                self.conn.execute("PRAGMA user_version = 2")
        if version < 3:
            with self.conn:
                self.conn.executescript(_DOWNLOADS_SCHEMA)
                self.conn.execute("PRAGMA user_version = 3")
        if version < 4:
            with self.conn:
                self.conn.executescript(_DISMISSALS_SCHEMA)
                self.conn.execute("PRAGMA user_version = 4")
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"{self.path.name} was written by a newer vidlib (schema {version}); "
                "update the toolbox before using it"
            )

    # ── Writing ──────────────────────────────────────────────────────

    def upsert(self, video: Video) -> None:
        """Insert or replace the row for video.path, and its people and tags."""
        name = video.name
        people_key, title_key, collection_key = name.key or ("", "", "")
        values = {c: getattr(video, c) for c in _VIDEO_COLUMNS}
        values["has_sub"] = int(video.has_sub)
        values["updated_at"] = video.updated_at or _now()
        values.update(
            people_key=people_key, title=name.title, title_key=title_key,
            collection=name.collection, collection_key=collection_key,
            resolution=video.resolution,
        )
        columns = list(values)
        with self.conn:
            self.conn.execute(
                f"INSERT INTO videos ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)}) "
                f"ON CONFLICT (path) DO UPDATE SET "
                + ", ".join(f"{c} = excluded.{c}" for c in columns if c != "path"),
                [values[c] for c in columns],
            )
            video_id: int = self.conn.execute(
                "SELECT id FROM videos WHERE path = ?", (video.path,)
            ).fetchone()[0]
            self._set_people(video_id, name.people)
            self._set_tags(video_id, name.tags)

    def _set_people(self, video_id: int, people: tuple[str, ...]) -> None:
        self.conn.execute("DELETE FROM video_people WHERE video_id = ?", (video_id,))
        for person in people:
            key = core.normalize(person)
            self.conn.execute(
                "INSERT INTO people (name, key) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                (person, key),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO video_people (video_id, person_id) "
                "SELECT ?, id FROM people WHERE key = ?",
                (video_id, key),
            )

    def _set_tags(self, video_id: int, tags: tuple[str, ...]) -> None:
        self.conn.execute("DELETE FROM video_tags WHERE video_id = ?", (video_id,))
        for tag in tags:
            self.conn.execute(
                "INSERT OR IGNORE INTO video_tags (video_id, tag, tag_key) VALUES (?, ?, ?)",
                (video_id, tag, core.normalize(tag)),
            )

    def move(self, old_path: str, new_path: str) -> None:
        """A file was renamed or moved: carry its row (and history) to the new path."""
        video = self.get(old_path)
        if video is None:
            return
        with self.conn:
            self.conn.execute("DELETE FROM videos WHERE path = ?", (new_path,))
            self.conn.execute("DELETE FROM videos WHERE path = ?", (old_path,))
        video.path = new_path
        video.updated_at = ""
        self.upsert(video)

    def remove(self, path: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM videos WHERE path = ?", (path,))
        self.prune_people()

    def prune_people(self) -> None:
        """Drop people no video mentions any more (renamed away, removed)."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM people WHERE id NOT IN (SELECT person_id FROM video_people)"
            )

    def log_event(self, path: str, action: str, detail: str = "") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO events (at, path, action, detail) VALUES (?, ?, ?, ?)",
                (_now(), path, action, detail),
            )

    # ── Reading ──────────────────────────────────────────────────────

    def _rows(self, where: str = "", params: tuple[object, ...] = ()) -> list[Video]:
        rows = self.conn.execute(
            f"SELECT {', '.join(_VIDEO_COLUMNS)} FROM videos {where} ORDER BY path COLLATE NOCASE",
            params,
        ).fetchall()
        return [Video(**{**dict(row), "has_sub": bool(row["has_sub"])}) for row in rows]

    def get(self, path: str) -> Video | None:
        found = self._rows("WHERE path = ?", (path,))
        return found[0] if found else None

    def all(self, include_missing: bool = False) -> list[Video]:
        return self._rows("" if include_missing else "WHERE status != 'missing'")

    def same_video(self, stem: str, exclude_path: str | None = None) -> list[Video]:
        """Every known copy of the video a filename stem names (same people, title and
        collection, whatever the order, case or tags), wherever it is in the library."""
        key = core.parse_name(stem).key
        if key is None:
            return []
        people_key, title_key, collection_key = key
        return self._rows(
            "WHERE people_key = ? AND title_key = ? AND collection_key = ? "
            "AND status NOT IN ('missing', 'failed') AND path != ?",
            (people_key, title_key, collection_key, exclude_path or ""),
        )

    def same_file(self, size: int, mtime: float) -> list[Video]:
        """Rows whose file had exactly this size and modification time: a rename or move
        keeps both, so this is how a file renamed by hand is recognised."""
        return self._rows("WHERE size = ? AND abs(mtime - ?) < 0.001", (size, mtime))

    def search(self, person: str | None = None, collection: str | None = None,
               resolution: str | None = None, tag: str | None = None,
               unnamed: bool = False) -> list[Video]:
        """Videos matching every filter given. Text filters are case-insensitive and match
        part of a value ("ali" finds Alice); resolution matches the rung ("720" = "720p")."""
        clauses = ["status != 'missing'"]
        if unnamed:
            # Names that don't follow People - Title - Collection have an empty match key.
            clauses.append("title_key = ''")
        params: list[object] = []
        if person:
            clauses.append(
                "EXISTS (SELECT 1 FROM video_people vp JOIN people p ON p.id = vp.person_id "
                "WHERE vp.video_id = videos.id AND p.key LIKE ? ESCAPE '\\')"
            )
            params.append(f"%{_escape_like(core.normalize(person))}%")
        if collection:
            clauses.append("collection_key LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(core.normalize(collection))}%")
        if resolution:
            clauses.append("resolution = ?")
            params.append(_resolution_query(resolution))
        if tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM video_tags t WHERE t.video_id = videos.id "
                "AND t.tag_key LIKE ? ESCAPE '\\')"
            )
            params.append(f"%{_escape_like(core.normalize(tag))}%")
        return self._rows("WHERE " + " AND ".join(clauses), tuple(params))

    def duplicate_groups(self) -> list[list[Video]]:
        """Sets of two or more files that are the same video, for reporting."""
        keys = self.conn.execute(
            "SELECT people_key, title_key, collection_key FROM videos "
            "WHERE status NOT IN ('missing', 'failed') AND title_key != '' "
            "GROUP BY people_key, title_key, collection_key HAVING count(*) > 1 "
            "ORDER BY title_key, collection_key"
        ).fetchall()
        return [
            self._rows(
                "WHERE people_key = ? AND title_key = ? AND collection_key = ? "
                "AND status NOT IN ('missing', 'failed')",
                tuple(k),
            )
            for k in keys
        ]

    # ── Downloads ────────────────────────────────────────────────────

    def save_download(self, dl: Download) -> Download:
        """Insert *dl* (id None, which is then set) or update its row."""
        dl.updated_at = _now()
        dl.added_at = dl.added_at or dl.updated_at
        values = [getattr(dl, c) for c in _DOWNLOAD_COLUMNS]
        with self.conn:
            if dl.id is None:
                cur = self.conn.execute(
                    f"INSERT INTO downloads ({', '.join(_DOWNLOAD_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in _DOWNLOAD_COLUMNS)})", values)
                dl.id = cur.lastrowid
            else:
                self.conn.execute(
                    f"UPDATE downloads SET {', '.join(f'{c} = ?' for c in _DOWNLOAD_COLUMNS)} "
                    "WHERE id = ?", [*values, dl.id])
        return dl

    def downloads(self) -> list[Download]:
        """Every download, oldest first."""
        rows = self.conn.execute("SELECT * FROM downloads ORDER BY id").fetchall()
        return [Download(**{k: r[k] for k in r.keys()}) for r in rows]

    def delete_download(self, download_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM downloads WHERE id = ?", (download_id,))

    # ── Parts ────────────────────────────────────────────────────────

    def videos_with_ids(self) -> list[tuple[int, Video]]:
        """Every video that could be a part: known, present, not waiting in review."""
        rows = self.conn.execute(
            "SELECT * FROM videos WHERE status NOT IN ('missing', 'review', 'failed') "
            "ORDER BY id").fetchall()
        return [(r["id"], Video(**{**{c: r[c] for c in _VIDEO_COLUMNS},
                                   "has_sub": bool(r["has_sub"])}))
                for r in rows]

    def dismiss_parts(self, video_ids: list[int]) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO part_dismissals (video_ids, at) VALUES (?, ?)",
                              (json.dumps(sorted(set(video_ids))), _now()))

    def part_dismissals(self) -> list[frozenset[int]]:
        return [frozenset(json.loads(r[0]))
                for r in self.conn.execute("SELECT video_ids FROM part_dismissals")]

    def spellings(self) -> tuple[list[str], list[str]]:
        """People and collections as the library writes them, for name suggestions: the
        people table, every video's collection, and the names of earlier downloads."""
        people = [r[0] for r in self.conn.execute("SELECT name FROM people ORDER BY id")]
        collections = [r[0] for r in self.conn.execute(
            "SELECT collection FROM videos WHERE collection != '' "
            "GROUP BY collection_key ORDER BY MIN(id)")]
        for (stem,) in self.conn.execute("SELECT name FROM downloads WHERE name != ''"):
            name = core.parse_name(stem)
            if name.complete:
                people.extend(name.people)
                collections.append(name.collection)
        return people, collections

    def events(self, path: str | None = None) -> Iterator[sqlite3.Row]:
        if path is None:
            yield from self.conn.execute("SELECT * FROM events ORDER BY id")
        else:
            yield from self.conn.execute("SELECT * FROM events WHERE path = ? ORDER BY id", (path,))


# ── One-time import of the old JSON log ────────────────────────────────


def _json_entry_path(name: str, entry: dict[str, object]) -> str:
    """Where a log entry's file lives: its recorded destination, _review/ for a parked
    file, else the root (awaiting a trip, skipped, failed, reconciled-but-unsorted)."""
    dest = entry.get("dest")
    if isinstance(dest, str) and dest:
        return dest.replace("\\", "/")
    if entry.get("status") == "review":
        return f"{REVIEW_DIR}/{name}"
    return name


def _num(entry: dict[str, object], key: str, default: float = 0) -> float:
    value = entry.get(key)
    return float(value) if isinstance(value, (int, float)) else default


def import_json_log(root: Path, db: LibraryDB, rename: bool = True) -> int:
    """Copy `.optimiselib.json` into the database, then rename it `.optimiselib.json.migrated`
    (unless `rename` is False, as for a dry run's in-memory database).

    Returns the number of entries imported (0 when there's no log). The old log's
    "reconciled" marker retires here: a reconciled file that was still waiting in the root
    becomes status "compressed" — known and already compressed — which is all the marker
    ever meant. Size and mtime are read from the file where it still exists, so a later hand
    rename can be recognised.
    """
    log_path = root / JSON_LOG_NAME
    if not log_path.is_file():
        return 0
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"can't read {log_path.name}: {exc}") from exc
    if not isinstance(log, dict):
        raise RuntimeError(f"{log_path.name} isn't a log object")

    imported = 0
    for name, raw in log.items():  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(name, str) or not isinstance(raw, dict):
            continue
        entry: dict[str, object] = raw  # pyright: ignore[reportUnknownVariableType]
        status = str(entry.get("status") or "sorted")
        if status == "reconciled":
            status = "compressed"
        if status not in STATUSES:
            status = "sorted"
        path = _json_entry_path(name, entry)

        video = Video(
            path=path,
            status=status,
            height=core.resolution_height(str(entry.get("resolution") or "")),
            bit_depth=int(_num(entry, "bit_depth", 8)),
            has_sub=bool(entry.get("has_sub")),
            duration=_num(entry, "duration"),
            size=int(_num(entry, "compressed_size")),
            source_height=int(_num(entry, "source_height")),
            original_size=int(_num(entry, "original_size")),
            backend=str(entry.get("backend") or ""),
            failures=int(_num(entry, "failures")),
            reason=str(entry.get("reason") or ""),
            compared_with=str(entry.get("compared_with") or ""),
            replaced=str(entry.get("replaced") or ""),
            updated_at=str(entry.get("ts") or ""),
        )
        try:
            st = (root / path).stat()
            video.size, video.mtime = st.st_size, st.st_mtime
        except OSError:
            pass
        db.upsert(video)
        imported += 1

    db.log_event(JSON_LOG_NAME, "migrated", f"{imported} entries")
    if rename:
        log_path.rename(log_path.with_name(log_path.name + MIGRATED_SUFFIX))
    return imported
