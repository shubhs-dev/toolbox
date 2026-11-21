"""The download manager behind vidlib's web UI: the queue, its statuses, and moving finished
downloads into the library.

Every download is a row in the library database's `downloads` table, so history lives with
the library. Each one runs in its own folder, `<dest>/_incoming/<id>/`, and is moved out once
yt-dlp finishes:

- normal mode (dest is the library root): straight into the root, where the pipeline picks
  it up as new input — status `imported`;
- download-only mode (dest is another folder, e.g. a disk with room): into that folder —
  status `downloaded` — until import_waiting() moves it into the library.

A file whose name is already taken where it's going stays where it is, `downloaded` with the
reason in `error`, for import_waiting() to retry once the clash is sorted out. Nothing is
ever overwritten.

All state sits behind one lock; engine callbacks arrive on worker threads and take it too.
Each change bumps `version` and notifies waiters, which is what the server's event stream
blocks on.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolboxcli.vidlib import core, fetcher, naming
from toolboxcli.vidlib.db import INCOMING_DIR, Download, LibraryDB

LEGACY_QUEUE_FILE = ".video-download-queue.json"
MIGRATED_SUFFIX = ".migrated"
# The statuses where a download is waiting on, or running in, the engine.
ACTIVE = ("queued", "downloading")
# Finished one way or another: what "clear finished" removes.
FINISHED = ("imported", "cancelled")


def move_file(src: Path, dest_dir: Path) -> Path:
    """Move *src* into *dest_dir* under its own name, never overwriting.

    On the same disk it's a rename. Across disks it's a copy to a hidden temp name (so the
    library scan never sees a half-copied file), a size check, and only then the rename into
    place and the source's removal. Raises FileExistsError when the name is taken, and
    FileNotFoundError when *dest_dir* doesn't exist — it's never created, since a missing
    library folder usually means its disk is unplugged."""
    dest = dest_dir / src.name
    if dest.exists():
        raise FileExistsError(f"{dest.name} already exists in {dest_dir}")
    if not dest_dir.is_dir():
        raise FileNotFoundError(f"{dest_dir} isn't there — is its disk connected?")
    try:
        if src.stat().st_dev == dest_dir.stat().st_dev:
            os.rename(src, dest)
            return dest
    except OSError:
        pass   # fall through to copying
    tmp = dest_dir / f".{src.name}.vidlib-partial"
    try:
        shutil.copy2(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            raise OSError(f"copying {src.name} to {dest_dir} came out the wrong size")
        if dest.exists():
            raise FileExistsError(f"{dest.name} already exists in {dest_dir}")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    src.unlink()
    return dest


def _video_of(files: list[Path]) -> Path | None:
    """The video among a finished job's files (yt-dlp may also leave subtitles, thumbnails)."""
    videos = [f for f in files if f.suffix.lower() in core.VIDEO_EXTS]
    return max(videos, key=lambda f: f.stat().st_size) if videos else None


@dataclass
class _Live:
    percent: float = 0.0
    speed: str = ""
    eta: str = ""


@dataclass
class DownloadManager:
    """Downloads for one library. Call start() once, shutdown() at the end."""

    library: Path                       # the library root, where .vidlib.db lives
    db: LibraryDB                       # opened with shared=True; used under self.lock only
    dest: Path | None = None            # download-only: where files go; None = the library
    workers: int = 2
    title_fetcher: Callable[[str], str] = fetcher.fetch_title
    lock: threading.RLock = field(default_factory=threading.RLock)
    version: int = 0

    def __post_init__(self) -> None:
        self.changed = threading.Condition(self.lock)
        self.items: dict[int, Download] = {}
        self.live: dict[int, _Live] = {}
        self.jobs: dict[int, fetcher.Job] = {}
        self.fetching: set[int] = set()
        self.engine = fetcher.Engine(self.workers, on_start=self._on_start,
                                     on_line=self._on_line, on_done=self._on_done)
        self._titles = ThreadPoolExecutor(max_workers=2)
        self._closing = False

    @property
    def download_only(self) -> bool:
        return self.dest is not None

    @property
    def target(self) -> Path:
        """Where finished downloads go: the download-only folder, or the library root."""
        return self.dest if self.dest is not None else self.library

    @property
    def staging(self) -> Path:
        return self.target / INCOMING_DIR

    # ── lifecycle ──

    def start(self) -> int:
        """Load the queue, import an old video-download queue file, and start what's queued.
        Returns how many entries the old queue file contributed."""
        with self.lock:
            imported = self._import_legacy_queue()
            for dl in self.db.downloads():
                assert dl.id is not None
                if dl.status == "downloading":   # interrupted: yt-dlp resumes its .part
                    dl.status = "queued"
                    self.db.save_download(dl)
                self.items[dl.id] = dl
            for dl in self.items.values():
                if dl.status == "queued":
                    self._submit(dl)
                elif dl.status == "naming" and not dl.page_title:
                    self._fetch_title(dl)
            self._bump()
        return imported

    def shutdown(self) -> None:
        """Stop every running download. Their rows stay `downloading`, which start() turns
        back into `queued` next time."""
        with self.lock:
            self._closing = True
        self.engine.shutdown(cancel=True, wait=False)
        self._titles.shutdown(wait=False, cancel_futures=True)

    def _import_legacy_queue(self) -> int:
        """Rows from video-download's `.video-download-queue.json` in the download folder,
        once; the file is then renamed `.migrated`."""
        path = self.target / LEGACY_QUEUE_FILE
        if not path.is_file():
            return 0
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        raw_items: Any = data.get("items") if isinstance(data, dict) else None
        count = 0
        for raw in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
                continue
            status = raw.get("status")
            dl = Download(url=raw["url"], name=str(raw.get("name") or ""),
                          error=str(raw.get("error") or ""), added_at=str(raw.get("added") or ""))
            if status == "done":
                # It was saved into this folder by name; find it so it can be imported.
                matches = [p for p in self.target.glob(f"{_glob_escape(dl.name)}.*")
                           if p.suffix.lower() in core.VIDEO_EXTS] if dl.name else []
                dl.status = "downloaded"
                dl.file = str(matches[0]) if len(matches) == 1 else ""
            elif status in ("failed", "cancelled"):
                dl.status = status
            else:
                dl.status = "queued"
            self.db.save_download(dl)
            count += 1
        path.rename(path.with_name(path.name + MIGRATED_SUFFIX))
        return count

    # ── change notification ──

    def _bump(self) -> None:
        self.version += 1
        self.changed.notify_all()

    def wait_for_change(self, seen: int, timeout: float) -> int:
        """Block until `version` moves past *seen* (or *timeout*); returns the version."""
        with self.lock:
            self.changed.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version

    # ── names ──

    def vocabulary(self) -> naming.Vocabulary:
        with self.lock:
            people, collections = self.db.spellings()
        return naming.Vocabulary.from_spellings(people, collections)

    # ── actions (each raises KeyError for an unknown id, ValueError for a bad request) ──

    def add(self, url: str, name: str, page_title: str = "") -> Download:
        url = url.strip()
        if not url:
            raise ValueError("a URL is needed")
        with self.lock:
            if any(d.url == url and d.status in (*ACTIVE, "naming")
                   for d in self.items.values()):
                raise ValueError("that URL is already in the queue")
            dl = self.db.save_download(Download(url=url, name=name.strip(),
                                                page_title=page_title, status="queued"))
            assert dl.id is not None
            self.items[dl.id] = dl
            self._submit(dl)
            self._bump()
            return dl

    def add_bulk(self, urls: list[str]) -> list[Download]:
        """Each new URL waits in `naming` while its title is fetched, to be named by hand."""
        added: list[Download] = []
        with self.lock:
            known = {d.url for d in self.items.values() if d.status not in FINISHED}
            for url in dict.fromkeys(u.strip() for u in urls):
                if not url or url in known:
                    continue
                dl = self.db.save_download(Download(url=url, status="naming"))
                assert dl.id is not None
                self.items[dl.id] = dl
                self._fetch_title(dl)
                added.append(dl)
            self._bump()
        return added

    def name_item(self, download_id: int, name: str, page_title: str | None = None) -> None:
        """Give a `naming` item its name and queue it."""
        with self.lock:
            dl = self.items[download_id]
            if dl.status != "naming":
                raise ValueError("that download isn't waiting for a name")
            dl.name = name.strip()
            if page_title is not None:
                dl.page_title = page_title
            dl.status, dl.error = "queued", ""
            self.db.save_download(dl)
            self._submit(dl)
            self._bump()

    def retry(self, download_id: int) -> None:
        with self.lock:
            dl = self.items[download_id]
            if dl.status not in ("failed", "cancelled"):
                raise ValueError("only a failed or cancelled download can be retried")
            dl.status, dl.error = "queued", ""
            self.db.save_download(dl)
            self._submit(dl)
            self._bump()

    def cancel(self, download_id: int) -> None:
        with self.lock:
            dl = self.items[download_id]
            job = self.jobs.get(download_id)
            if job is None:
                raise ValueError("that download isn't running")
            self.engine.cancel(job)
            if dl.status == "queued":   # never started: its job ends without running
                dl.status = "cancelled"
                self.db.save_download(dl)
                self._bump()

    def remove(self, download_id: int) -> None:
        """Forget a download that isn't running. Its files, wherever they are, stay put."""
        with self.lock:
            if download_id not in self.items:
                raise KeyError(download_id)
            if download_id in self.jobs:
                raise ValueError("cancel it first")
            self.db.delete_download(download_id)
            del self.items[download_id]
            self.live.pop(download_id, None)
            self._bump()

    def clear_finished(self) -> int:
        with self.lock:
            done = [i for i, d in self.items.items() if d.status in FINISHED]
            for i in done:
                self.db.delete_download(i)
                del self.items[i]
                self.live.pop(i, None)
            self._bump()
            return len(done)

    def waiting(self) -> list[Download]:
        """Downloaded files not yet in the library (download-only mode, or a name clash)."""
        with self.lock:
            return [d for d in self.items.values()
                    if d.status == "downloaded" and d.file and Path(d.file).is_file()]

    def import_waiting(self) -> tuple[int, list[str]]:
        """Move every waiting download into the library root: (moved, problems)."""
        moved, problems = 0, []
        for dl in self.waiting():
            try:
                self._import(dl, Path(dl.file))
                moved += 1
            except OSError as exc:
                problems.append(str(exc))
                with self.lock:
                    dl.error = str(exc)
                    self.db.save_download(dl)
        with self.lock:
            self._bump()
        return moved, problems

    def _import(self, dl: Download, file: Path) -> None:
        """Move *file* (and any sidecars beside it in its job folder) into the library root."""
        folder = file.parent
        final = move_file(file, self.library)
        if folder.parent == self.staging:
            self._move_sidecars(folder, self.library)
        with self.lock:
            dl.status, dl.error, dl.file = "imported", "", ""
            dl.library_path = final.relative_to(self.library).as_posix()
            self.db.save_download(dl)

    def _move_sidecars(self, folder: Path, dest_dir: Path) -> None:
        for extra in fetcher.finished_files(folder):
            try:
                move_file(extra, dest_dir)
            except OSError:
                pass   # a subtitle or thumbnail that can't move isn't worth failing over
        try:
            folder.rmdir()
        except OSError:
            pass

    # ── the engine ──

    def _submit(self, dl: Download) -> None:
        assert dl.id is not None
        self.live[dl.id] = _Live()
        job = fetcher.Job(id=dl.id, url=dl.url, folder=self.staging / str(dl.id),
                          name=dl.name or None)
        self.jobs[dl.id] = job
        self.engine.submit(job)

    def _fetch_title(self, dl: Download) -> None:
        assert dl.id is not None
        download_id, url = dl.id, dl.url
        self.fetching.add(download_id)

        def work() -> None:
            try:
                title, error = self.title_fetcher(url), ""
            except RuntimeError as exc:
                title, error = "", str(exc)
            with self.lock:
                self.fetching.discard(download_id)
                item = self.items.get(download_id)
                if item is not None and item.status == "naming":
                    item.page_title, item.error = title, error
                    self.db.save_download(item)
                self._bump()

        self._titles.submit(work)

    def _job_item(self, job: fetcher.Job) -> Download | None:
        download_id = job.id
        assert isinstance(download_id, int)
        if self.jobs.get(download_id) is not job:
            return None   # retried since; this is the old job's news
        return self.items.get(download_id)

    def _on_start(self, job: fetcher.Job) -> None:
        with self.lock:
            dl = self._job_item(job)
            if dl is not None:
                dl.status = "downloading"
                self.db.save_download(dl)
                self._bump()

    def _on_line(self, job: fetcher.Job, _line: str, prog: fetcher.Progress | None) -> None:
        if prog is None:
            return
        with self.lock:
            dl = self._job_item(job)
            if dl is not None and dl.id is not None:
                self.live[dl.id] = _Live(prog.percent, prog.speed, prog.eta)
                self._bump()

    def _on_done(self, job: fetcher.Job) -> None:
        with self.lock:
            dl = self._job_item(job)
            if dl is None or dl.id is None or self._closing:
                return   # shutting down: the row keeps its status, to resume next time
            del self.jobs[dl.id]
            if job.cancelled:
                dl.status = "cancelled"
            elif job.returncode != 0:
                dl.status = "failed"
                dl.error = job.tail[-1] if job.tail else f"yt-dlp exited {job.returncode}"
                if not self.target.is_dir():
                    dl.error = f"{self.target} isn't there — is its disk connected?"
            else:
                self._deliver(dl, job.folder)
            self.db.save_download(dl)
            self._bump()

    def _deliver(self, dl: Download, folder: Path) -> None:
        """A finished job's file, out of its folder: into the library, or the download-only
        folder. Called under the lock."""
        video = _video_of(fetcher.finished_files(folder))
        if video is None:
            dl.status, dl.error = "failed", "yt-dlp finished but left no video file"
            return
        dl.status, dl.error, dl.file = "downloaded", "", str(video)
        try:
            if self.dest is None:
                self._import(dl, video)
                return
            final = move_file(video, self.dest)
            self._move_sidecars(folder, self.dest)
            dl.file = str(final)
        except OSError as exc:
            dl.error = f"{exc} — left in {folder}"

    # ── what the UI shows ──

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            rows: list[dict[str, Any]] = []
            for i, dl in self.items.items():
                live = self.live.get(i, _Live())
                rows.append({
                    "id": i, "url": dl.url, "name": dl.name, "page_title": dl.page_title,
                    "status": dl.status, "error": dl.error, "file": dl.file,
                    "library_path": dl.library_path, "added_at": dl.added_at,
                    "fetching": i in self.fetching,
                    "percent": 100.0 if dl.status in ("downloaded", "imported") else live.percent,
                    "speed": live.speed if dl.status == "downloading" else "",
                    "eta": live.eta if dl.status == "downloading" else "",
                })
            return {
                "version": self.version,
                "mode": "download-only" if self.download_only else "library",
                "library": str(self.library),
                "target": str(self.target),
                "downloads": rows,
                "overall": _overall(rows),
            }


def _overall(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The batch's progress: what's queued, running or finished since it was last cleared,
    counting a running download by its percentage. Failed and cancelled ones don't count."""
    counted = [r for r in rows if r["status"] in (*ACTIVE, "downloaded", "imported")]
    done = sum(1 for r in counted if r["status"] in ("downloaded", "imported"))
    running = [r for r in counted if r["status"] == "downloading"]
    progress = done + sum(r["percent"] / 100 for r in running)
    return {
        "total": len(counted), "done": done, "running": len(running),
        "queued": sum(1 for r in counted if r["status"] == "queued"),
        "failed": sum(1 for r in rows if r["status"] == "failed"),
        "naming": sum(1 for r in rows if r["status"] == "naming"),
        "percent": round(100 * progress / len(counted), 1) if counted else 0.0,
        "speed": sum(fetcher.rate_bytes(r["speed"]) for r in running),
    }


def _glob_escape(text: str) -> str:
    return "".join(f"[{c}]" if c in "*?[" else c for c in text)
