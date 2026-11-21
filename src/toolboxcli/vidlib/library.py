"""The web page's view of the library: browsing and filtering what the database knows,
thumbnails, and the per-video actions (play, show in Finder, rename, move to Trash).

Every action takes a root-relative path the *database* knows; nothing outside the library is
ever read, opened or moved. It shares the download manager's database connection and lock
(the server's threads all go through them), next to the pipeline's own connection on the
main thread — WAL keeps the two consistent, and the pipeline's next sync sees what changed
here as already done. A rename moves the file and its row together (db.move), so its
history follows it, just as the pipeline's own sync would match a hand rename.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from toolboxcli._common.tooling import detached_kwargs
from toolboxcli._common.trash import move_to_trash
from toolboxcli.vidlib import naming
from toolboxcli.vidlib.db import LibraryDB, Video
from toolboxcli.vidlib.status import PipelineStatus

CACHE_DIR = ".vidlib-cache"
THUMB_WIDTH = 480
PAGE_SIZE = 60
VIEWS = ("all", "duplicates", "unnamed", "review")


class LibraryError(Exception):
    """A request the library can't carry out; the message says why, for the page."""


def _video_json(v: Video) -> dict[str, Any]:
    name = v.name
    return {
        "path": v.path, "filename": v.filename, "folder": str(PurePosixPath(v.path).parent),
        "people": list(name.people), "title": name.title, "collection": name.collection,
        "tags": list(name.tags), "complete": name.complete, "status": v.status,
        "resolution": v.resolution, "height": v.height, "bit_depth": v.bit_depth,
        "duration": v.duration, "size": v.size, "has_sub": v.has_sub,
        "reason": v.reason, "compared_with": v.compared_with, "updated_at": v.updated_at,
    }


@dataclass
class Library:
    root: Path
    db: LibraryDB
    lock: threading.RLock
    pipeline: PipelineStatus | None = None

    def __post_init__(self) -> None:
        self._thumbs = threading.BoundedSemaphore(2)   # ffmpeg runs at once, at most

    # ── browsing ──

    def query(self, view: str = "all", q: str = "", person: str = "", collection: str = "",
              resolution: str = "", tag: str = "", offset: int = 0,
              limit: int = PAGE_SIZE) -> dict[str, Any]:
        if view not in VIEWS:
            raise LibraryError(f"view is one of {', '.join(VIEWS)}")
        with self.lock:
            if view == "duplicates":
                groups = self.db.duplicate_groups()
                videos = [v for g in groups for v in g]
                group_of = {v.path: i for i, g in enumerate(groups) for v in g}
            else:
                videos = self.db.search(person=person or None, collection=collection or None,
                                        resolution=resolution or None, tag=tag or None,
                                        unnamed=view == "unnamed")
                group_of = {}
            facets = self._facets()
        if view == "review":
            videos = [v for v in videos if v.status == "review"]
        elif view in ("all", "unnamed"):
            videos = [v for v in videos if v.status != "review"]
        if q.strip():
            needle = naming.loose_key(q)
            videos = [v for v in videos if needle in naming.loose_key(v.filename)]
        if view != "duplicates":
            videos.sort(key=lambda v: v.filename.casefold())
        page = videos[offset:offset + limit]
        rows = [_video_json(v) | ({"group": group_of[v.path]} if group_of else {}) for v in page]
        return {"total": len(videos), "offset": offset, "videos": rows, **facets}

    def _facets(self) -> dict[str, Any]:
        """People and collections with how many videos each has, for the filters."""
        people = self.db.conn.execute(
            "SELECT p.name, count(*) FROM people p JOIN video_people vp ON vp.person_id = p.id "
            "JOIN videos v ON v.id = vp.video_id WHERE v.status != 'missing' "
            "GROUP BY p.id ORDER BY p.name COLLATE NOCASE").fetchall()
        collections = self.db.conn.execute(
            "SELECT min(collection), count(*) FROM videos WHERE collection != '' "
            "AND status != 'missing' GROUP BY collection_key "
            "ORDER BY min(collection) COLLATE NOCASE").fetchall()
        counts = self.db.conn.execute(
            "SELECT count(*), coalesce(sum(size), 0) FROM videos "
            "WHERE status NOT IN ('missing', 'review')").fetchone()
        return {
            "people": [{"name": n, "count": c} for n, c in people],
            "collections": [{"name": n, "count": c} for n, c in collections],
            "library": {"videos": counts[0], "size": counts[1]},
        }

    # ── one video ──

    def _known(self, path: str) -> tuple[Video, Path]:
        """The row and file for a root-relative path the database knows, or LibraryError."""
        with self.lock:
            row = self.db.get(path)
        if row is None or row.status == "missing":
            raise LibraryError("the library doesn't know that file")
        file = self.root / row.path
        if not file.is_file():
            raise LibraryError(f"{row.path} isn't there any more")
        return row, file

    def _not_busy(self, row: Video) -> None:
        current = self.pipeline.snapshot()["current"] if self.pipeline else None
        if current and current["file"] == row.filename and "/" not in row.path:
            raise LibraryError("the pipeline is working on that file right now")

    def thumbnail(self, path: str) -> bytes:
        """A JPEG frame from a tenth of the way in, made once and cached by path, size and
        modified time (so a re-encoded file gets a fresh one)."""
        row, file = self._known(path)
        st = file.stat()
        key = hashlib.sha1(f"{row.path}\0{st.st_size}\0{st.st_mtime}".encode()).hexdigest()
        cache = self.root / CACHE_DIR / "thumbs"
        thumb = cache / f"{key}.jpg"
        if thumb.is_file():
            return thumb.read_bytes()
        with self._thumbs:
            if not thumb.is_file():
                cache.mkdir(parents=True, exist_ok=True)
                tmp = cache / f".{key}.tmp.jpg"
                # A tenth of the way in skips black intros; the first frame is the fallback
                # for a file shorter than its recorded duration.
                for at in dict.fromkeys((max(row.duration * 0.1, 0.0), 0.0)):
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(file),
                         "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "5",
                         str(tmp)],
                        capture_output=True, timeout=60, check=False, **detached_kwargs(),
                    )
                    if tmp.is_file() and tmp.stat().st_size > 0:
                        break
                else:
                    tmp.unlink(missing_ok=True)
                    raise LibraryError("ffmpeg couldn't read a frame from it")
                os.replace(tmp, thumb)
        return thumb.read_bytes()

    def open(self, path: str, reveal: bool = False) -> None:
        """Play the file in its default app, or show it in Finder / Explorer."""
        _, file = self._known(path)
        if sys.platform == "darwin":
            cmd = ["open", "-R", str(file)] if reveal else ["open", str(file)]
        elif sys.platform == "win32":
            if reveal:
                cmd = ["explorer", f"/select,{file}"]
            else:
                os.startfile(file)
                return
        else:
            cmd = ["xdg-open", str(file.parent if reveal else file)]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **detached_kwargs())
        except OSError as exc:
            raise LibraryError(f"couldn't open it: {exc}") from exc

    def rename(self, path: str, people: str, title: str, collection: str) -> dict[str, Any]:
        """Give a video a new People - Title - Collection name, keeping its [tags], and move
        it into its collection's folder (an existing one, matched as the pipeline matches
        it, or a new one). Its row moves with it."""
        from toolboxcli.vidlib.cli import find_folder  # cli imports the server, which imports this

        row, file = self._known(path)
        self._not_busy(row)
        with self.lock:
            people_list, collections = self.db.spellings()
        vocab = naming.Vocabulary.from_spellings(people_list, collections)
        draft = naming.from_fields(people, title, collection, vocab)
        if draft.problems():
            raise LibraryError(draft.problems()[0])
        assert draft.collection is not None
        tags = row.name.tags
        stem = draft.stem + (f" [{' '.join(tags)}]" if tags else "")
        if row.status == "review":
            folder = file.parent                          # stays parked until decided
        else:
            folder = find_folder(self.root, draft.collection.text) or (
                self.root / draft.collection.text)
        dest = folder / f"{stem}{file.suffix}"
        if dest == file:
            return _video_json(row)
        if dest.exists() and not _same_file(dest, file):
            raise LibraryError(f"{dest.relative_to(self.root).as_posix()} already exists")
        folder.mkdir(exist_ok=True)   # only ever a folder directly under the library root
        os.rename(file, dest)
        new_path = dest.relative_to(self.root).as_posix()
        with self.lock:
            self.db.move(row.path, new_path)
            self.db.log_event(new_path, "renamed", f"from {row.path} (web page)")
            moved = self.db.get(new_path)
        assert moved is not None
        return _video_json(moved)

    def trash(self, path: str) -> None:
        """Move the file to the Trash (never deleted outright) and forget its row."""
        row, file = self._known(path)
        self._not_busy(row)
        move_to_trash(str(file))
        with self.lock:
            self.db.remove(row.path)
            self.db.log_event(row.path, "trashed", "from the web page")

    def video(self, path: str) -> dict[str, Any]:
        row, _ = self._known(path)
        info = _video_json(row)
        if row.status == "review" and row.compared_with:
            with self.lock:
                other = self.db.get(row.compared_with)
            info["library_copy"] = _video_json(other) if other else None
        return info


def _same_file(a: Path, b: Path) -> bool:
    """A case-only rename on a case-insensitive disk: dest "exists" because it *is* the file."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False
