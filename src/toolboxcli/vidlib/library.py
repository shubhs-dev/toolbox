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

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from toolboxcli._common.tooling import detached_kwargs
from toolboxcli._common.trash import move_to_trash
from toolboxcli.vidlib import core, index, naming, parts
from toolboxcli.vidlib.db import DUPLICATE_WHY, IGNORE_FILE, LibraryDB, Video
from toolboxcli.vidlib.status import PipelineStatus

STRIP_FRAMES = index.STRIP_FRAMES
PAGE_SIZE = 60
VIEWS = ("all", "duplicates", "unnamed", "review")

# The Library tab's sort orders, each with the direction it starts in (True = descending).
# "added" is the file's mtime: when it was downloaded or last encoded into the library —
# vidlib doesn't know when a video was watched. The duplicates view keeps its grouping.
SORTS: dict[str, bool] = {
    "name": False, "added": True, "people": False, "collection": False, "size": True,
    "duration": True, "resolution": True,
}


def _sort_key(sort: str) -> Callable[[Video], Any]:
    match sort:
        case "added":
            return lambda v: v.mtime
        case "people":
            return lambda v: ", ".join(v.name.people).casefold()
        case "collection":
            return lambda v: v.name.collection.casefold()
        case "size":
            return lambda v: v.size
        case "duration":
            return lambda v: v.duration
        case "resolution":
            return lambda v: v.height
        case _:
            return lambda v: v.filename.casefold()


def sort_videos(videos: list[Video], sort: str = "name", desc: bool | None = None) -> list[Video]:
    """`videos` in the order asked for, ties broken by file name. Videos with nothing to
    sort by (no people, no collection, an unprobed size or length) go last either way."""
    if sort not in SORTS:
        raise LibraryError(f"sort is one of {', '.join(SORTS)}")
    if desc is None:
        desc = SORTS[sort]
    key = _sort_key(sort)
    by_name = sorted(videos, key=lambda v: v.filename.casefold())
    known = [v for v in by_name if key(v) not in ("", 0, 0.0)]
    unknown = [v for v in by_name if key(v) in ("", 0, 0.0)]
    return sorted(known, key=key, reverse=desc) + unknown


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
              limit: int = PAGE_SIZE, sort: str = "name",
              desc: bool | None = None) -> dict[str, Any]:
        if view not in VIEWS:
            raise LibraryError(f"view is one of {', '.join(VIEWS)}")
        with self.lock:
            if view == "duplicates":
                sets = self.db.duplicate_sets()
                videos = [v for _why, g in sets for v in g]
                group_of = {v.path: i for i, (_why, g) in enumerate(sets) for v in g}
                why_of = {v.path: why for why, g in sets for v in g}
                members_of = {v.path: [m.path for m in g] for _why, g in sets for v in g}
            else:
                # The page's person and collection come from its lists: exactly that one,
                # not every name containing it. The search box (`q`) is the partial match.
                videos = self.db.search(person=person or None, collection=collection or None,
                                        resolution=resolution or None, tag=tag or None,
                                        unnamed=view == "unnamed", exact=True)
                group_of, why_of, members_of = {}, {}, {}
            facets = self._facets()
        if view == "review":
            videos = [v for v in videos if v.status == "review"]
        elif view in ("all", "unnamed"):
            videos = [v for v in videos if v.status != "review"]
        if q.strip():
            needle = naming.loose_key(q)
            videos = [v for v in videos if needle in naming.loose_key(v.filename)]
        if sort not in SORTS:
            raise LibraryError(f"sort is one of {', '.join(SORTS)}")
        if desc is None:
            desc = SORTS[sort]
        if view != "duplicates":
            videos = sort_videos(videos, sort, desc)
        page = videos[offset:offset + limit]
        with self.lock:
            ids = {path: i for i, path in self.db.conn.execute("SELECT id, path FROM videos")}
        rows = [_video_json(v) | {"id": ids.get(v.path)}
                | ({"group": group_of[v.path], "why": why_of[v.path],
                    "why_text": DUPLICATE_WHY[why_of[v.path]],
                    # the whole set's ids, though a page may show only some of it
                    "group_ids": [ids[m] for m in members_of[v.path] if m in ids]}
                   if group_of else {})
                for v in page]
        return {"total": len(videos), "offset": offset, "videos": rows, "sort": sort,
                "desc": desc, **facets}

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

    def prepare(self, video: Video, runner: index.Runner | None = None) -> bool:
        """Make whatever a video is missing — thumbnail, filmstrip, fingerprint — in one
        ffmpeg run (index.render), at most two runs at once across the page's requests and
        the background indexer. The fingerprint is stored only if the row still describes
        the file that was read. False if nothing could be read from it."""
        file = self.root / video.path
        try:
            st = file.stat()
        except OSError:
            return False
        thumb, strip = index.cache_paths(self.root, st.st_size, st.st_mtime)
        with self._thumbs:
            want_strip = video.duration > 0 and not strip.is_file()
            want_fp = video.duration > 0 and not video.fingerprint
            if thumb.is_file() and not want_strip and not want_fp:
                return True
            fingerprint = index.render(file, video.duration,
                                       None if thumb.is_file() else thumb,
                                       strip if want_strip else None, runner)
        if fingerprint and want_fp:
            with self.lock:
                self.db.set_fingerprint(video.path, st.st_size, st.st_mtime, fingerprint)
        return thumb.is_file()

    def _image(self, path: str, strip: bool) -> bytes:
        row, file = self._known(path)
        st = file.stat()
        thumb_file, strip_file = index.cache_paths(self.root, st.st_size, st.st_mtime)
        wanted = strip_file if strip else thumb_file
        if strip and row.duration <= 0:
            raise LibraryError("no length known, so no preview")
        if not wanted.is_file():
            if strip:
                self.prepare(row)
            else:
                with self._thumbs:     # one quick frame; the indexer makes the rest
                    if not wanted.is_file():
                        index.make_thumbnail(file, row.duration, wanted)
        if not wanted.is_file():
            raise LibraryError("ffmpeg couldn't read a frame from it")
        return wanted.read_bytes()

    def thumbnail(self, path: str) -> bytes:
        """A JPEG frame from a tenth of the way in, made once and cached."""
        return self._image(path, strip=False)

    def filmstrip(self, path: str) -> bytes:
        """STRIP_FRAMES frames spread across the video, side by side in one JPEG, for the
        hover preview — the frames the fingerprint is made from."""
        return self._image(path, strip=True)

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
        draft = self._draft(*naming.fill_unknown(people, title, collection))
        assert draft.collection is not None
        tags = row.name.tags
        stem = draft.stem + (f" [{' '.join(tags)}]" if tags else "")
        if row.status == "review":
            folder = file.parent                          # stays parked until decided
        else:
            folder = find_folder(self.root, draft.collection.text) or (
                self.root / draft.collection.text)
            if (folder / IGNORE_FILE).is_file():
                raise LibraryError(f"{folder.name}/ is marked {IGNORE_FILE} — it isn't part "
                                   "of the library, so nothing is filed into it")
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

    def same_as(self, stem: str, exclude_path: str | None = None) -> list[str]:
        """Paths of videos the library would take for the same video as *stem* — duplicate
        detection would then compare them, and replace or park the worse one."""
        if not stem:
            return []
        with self.lock:
            return [v.path for v in self.db.same_video(stem, exclude_path)]

    # ── parts of one video ──

    def part_groups(self) -> list[dict[str, Any]]:
        """Sets of library videos that look like parts of one (parts.find_groups), each part
        with its marker, and the name the merged video would get."""
        with self.lock:
            rows = self.db.videos_with_ids()
            dismissed = self.db.part_dismissals()
            known_people, _ = self.db.spellings()
        by_id = dict(rows)
        videos = [parts.Video(i, v.name.title, tuple(v.name.people), v.name.collection)
                  for i, v in rows]
        groups: list[dict[str, Any]] = []
        for g in parts.find_groups(videos, dismissed=dismissed, people=known_people):
            groups.append({
                "sure": g.sure, "gaps": g.gaps, "doubled": g.doubled,
                "ids": sorted(g.ids),
                "name": {"people": ", ".join(g.people), "title": g.title,
                         "collection": g.collection},
                "members": [_video_json(by_id[m.video.id]) | {
                    "id": m.video.id, "first": m.part.marker.first,
                    "last": m.part.marker.last, "total": m.part.marker.total,
                } for m in g.members],
            })
        return groups

    def dismiss_duplicates(self, video_ids: list[int]) -> None:
        """"Not the same video" for a set the fingerprints matched: it stays out of the
        Duplicates view until another video joins it."""
        if len(set(video_ids)) < 2:
            raise LibraryError("pick at least two videos")
        with self.lock:
            self.db.dismiss_duplicates(video_ids)

    def dismiss_parts(self, video_ids: list[int]) -> None:
        """Remember that these videos aren't parts of one, so the set isn't offered again."""
        if len(set(video_ids)) < 2:
            raise LibraryError("a set of parts has at least two videos")
        with self.lock:
            self.db.dismiss_parts(video_ids)

    def _merge_rows(self, ids: list[int]) -> list[tuple[int, Video]]:
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise LibraryError("pick at least two different videos to merge")
        with self.lock:
            by_id = dict(self.db.videos_with_ids())
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise LibraryError("one of those videos isn't in the library any more")
        rows = [(i, by_id[i]) for i in ids]
        for _, row in rows:
            if not (self.root / row.path).is_file():
                raise LibraryError(f"{row.path} isn't there any more")
            self._not_busy(row)
        return rows

    def merge_plan(self, ids: list[int]) -> dict[str, Any]:
        """What merging these videos, in this order, would do: the proposed name, and what
        concatvid would have to re-encode (from the same checks it makes itself)."""
        from toolboxcli.concatvid import cli as concat_cli, core as concat_core

        rows = self._merge_rows(ids)
        people, title, collection = parts.proposal(
            [parts.Video(i, v.name.title, tuple(v.name.people), v.name.collection)
             for i, v in rows])
        notes: list[dict[str, str]] = []
        blocking = False
        files = [self.root / v.path for _, v in rows]
        infos = concat_cli.probe_group(files)
        if infos is None:
            notes.append({"level": "warn", "text": "Couldn't read every part with ffprobe, so "
                          "what needs re-encoding isn't known until the merge runs."})
        else:
            streams = [infos[f] for f in files]
            if concat_core.audio_presence_mismatch(streams):
                blocking = True
                notes.append({"level": "block", "text": "Some parts have sound and others "
                              "don't, so they can't be joined."})
            else:
                target = concat_core.choose_target(streams)
                extras = any(i.has_extra_streams for i in streams)
                changed = False
                for n, (info, (_, row)) in enumerate(zip(streams, rows, strict=True), 1):
                    if not concat_core.video_matches(info, target):
                        changed = True
                        size = (f" (down from {info.height}p)"
                                if info.height > target.height else "")
                        notes.append({"level": "warn", "text": f"Part {n} ({row.filename}) "
                                      f"will be re-encoded to {target.height}p{size}."})
                    elif not concat_core.audio_matches(info, target):
                        changed = True
                        notes.append({"level": "info", "text": f"Part {n}'s sound will be "
                                      "re-encoded to match the others."})
                if changed and extras:
                    notes.append({"level": "warn", "text": "Extra audio and subtitle tracks "
                                  "will be dropped, so every part has the same tracks."})
                if not changed:
                    notes.append({"level": "ok", "text": "The parts match, so they're joined "
                                  "as they are — no re-encoding."})
        suffixes = {Path(v.path).suffix.lower() for _, v in rows}
        return {
            "ids": [i for i, _ in rows],
            "members": [_video_json(v) | {"id": i} for i, v in rows],
            "name": {"people": ", ".join(people), "title": title, "collection": collection},
            "ext": suffixes.pop() if len(suffixes) == 1 else ".mkv",
            "notes": notes, "blocking": blocking,
        }

    def queue_merge(self, ids: list[int], people: str, title: str,
                    collection: str) -> dict[str, Any]:
        """Queue the merge on the pipeline, which runs it between encodes."""
        if self.pipeline is None:
            raise LibraryError("nothing is merged in download-only mode")
        plan = self.merge_plan(ids)
        if plan["blocking"]:
            raise LibraryError(next(n["text"] for n in plan["notes"] if n["level"] == "block"))
        draft = self._draft(*naming.fill_unknown(people, title, collection))
        paths = [m["path"] for m in plan["members"]]
        try:
            job = self.pipeline.queue_merge(paths, draft.stem, plan["ext"])
        except ValueError as exc:
            raise LibraryError(str(exc)) from exc
        return job.to_json()

    def discard_merge(self, job_id: int) -> None:
        """Send an unverified join (its parts were kept) to the Trash."""
        if self.pipeline is None:
            raise LibraryError("nothing is merged in download-only mode")
        try:
            job = self.pipeline.merge(job_id)
        except KeyError as exc:
            raise LibraryError("no such merge") from exc
        if job.state != "unverified" or not job.output:
            raise LibraryError("only a merge that couldn't be checked can be discarded")
        out = self.root / job.output
        if out.is_file():
            move_to_trash(str(out))
        try:
            out.parent.rmdir()
        except OSError:
            pass
        self.pipeline.update_merge(job, state="discarded", detail="sent to the Trash")

    # ── the review queue ──

    def review_count(self) -> int:
        with self.lock:
            return self.db.conn.execute(
                "SELECT count(*) FROM videos WHERE status = 'review'").fetchone()[0]

    def review_items(self) -> list[dict[str, Any]]:
        """Everything parked in _review/, each with the library copy it was compared with
        (None when that copy has gone since)."""
        with self.lock:
            rows = [v for v in self.db.all() if v.status == "review"]
            copies = {v.path: self.db.get(v.compared_with) for v in rows if v.compared_with}
        items: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda v: v.filename.casefold()):
            copy = copies.get(row.path)
            items.append(_video_json(row) | {
                "library_copy": _video_json(copy) if copy and copy.status != "missing" else None,
            })
        return items

    def decide(self, path: str, keep: str, people: str = "", title: str = "",
               collection: str = "") -> None:
        """Settle a parked file against its library copy.

        new   the parked file replaces the library copy (which goes to the Trash)
        old   the library copy stays; the parked file goes to the Trash
        both  the parked file is renamed (people/title/collection) so it's no longer the
              same video, and kept as well

        A kept parked file goes back to the library root, where the pipeline files it — and,
        if it was parked before ever being encoded (a downgrade caught up front), encodes it
        first."""
        row, file = self._known(path)
        if row.status != "review":
            raise LibraryError("that file isn't waiting in review")
        with self.lock:
            copy = self.db.get(row.compared_with) if row.compared_with else None
        if keep == "old":
            move_to_trash(str(file))
            with self.lock:
                self.db.remove(row.path)
                self.db.log_event(row.path, "trashed", "review: kept the library copy")
            return
        if keep == "new":
            stem = file.stem
        elif keep == "both":
            draft = self._draft(people, title, collection)
            if copy is not None and copy.name.key == core.parse_name(draft.stem).key:
                raise LibraryError("that's still the same video as the library copy — change "
                                   "the title (or people) to keep both")
            tags = row.name.tags
            stem = draft.stem + (f" [{' '.join(tags)}]" if tags else "")
        else:
            raise LibraryError("keep is new, old or both")
        dest = self.root / f"{stem}{file.suffix}"
        if dest.exists():
            raise LibraryError(f"{dest.name} is already in the library root")
        if keep == "new" and copy is not None and copy.status != "missing":
            old = self.root / copy.path
            if old.is_file():
                move_to_trash(str(old))
            with self.lock:
                self.db.remove(copy.path)
                self.db.log_event(copy.path, "replaced", f"by {row.filename} (review)")
        os.rename(file, dest)
        new_path = dest.name
        with self.lock:
            self.db.move(row.path, new_path)
            if row.reason == core.DOWNGRADE and not row.backend:
                # Parked before its encode: forget it, so the pipeline takes it as new input.
                self.db.remove(new_path)
            else:
                moved = self.db.get(new_path)
                assert moved is not None
                moved.status, moved.reason, moved.compared_with = "compressed", "", ""
                self.db.upsert(moved)
            self.db.log_event(new_path, "released", f"review: kept {keep}")

    def _draft(self, people: str, title: str, collection: str) -> naming.Draft:
        with self.lock:
            people_list, collections = self.db.spellings()
        vocab = naming.Vocabulary.from_spellings(people_list, collections)
        draft = naming.from_fields(people, title, collection, vocab)
        if draft.problems():
            raise LibraryError(draft.problems()[0])
        return draft

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
