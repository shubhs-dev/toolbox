"""What the encode pipeline is doing, shared between its loop and the web page.

The pipeline runs on the main thread; the web server reads `snapshot()` from its own
threads and sends pause / stop requests back. Everything sits behind one lock, and every
change calls `on_change` (the download manager's notifier), which wakes the page's event
stream. `sleep()` is how the loop waits between polls: a resume or stop request cuts it
short.

The loop's states:
    starting       before the first poll
    scanning       syncing the library and looking for new files
    encoding       a batch is running (see `current` and `waiting`)
    idle           nothing to do until the next poll
    paused         paused from the page; the current file, if any, finished first
    outside_hours  -H: waiting for the encode window
    high_load      -L: waiting for the load average to drop
    disk_missing   the library root isn't there (its disk is unplugged)
    merging        joining parts queued from the page (see `merges`)

Merges queue here too, so they run on the pipeline's thread: one heavy job at a time, and
Pause / Stop now / -H apply to them. They're kept in memory only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

RECENT = 30
MERGE_STATES = ("queued", "running", "done", "failed", "unverified", "stopped", "discarded")


@dataclass
class MergeJob:
    id: int
    paths: list[str]           # root-relative, in the order to join them
    stem: str                  # the merged video's name, without extension
    ext: str
    state: str = "queued"
    detail: str = ""
    output: str = ""           # root-relative path of the joined file, once there is one

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "paths": self.paths, "name": self.stem + self.ext,
                "state": self.state, "detail": self.detail, "output": self.output}


def _noop() -> None:
    pass


class PipelineStatus:
    def __init__(self, on_change: Callable[[], None] = _noop,
                 settings: dict[str, Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.on_change = on_change
        self.cancel = threading.Event()     # handed to transcode(): set to stop an encode
        self.settings = settings or {}
        self.state = "starting"
        self.detail = ""
        self.paused = False
        self.batch_total = 0
        self.batch_done = 0
        self.current: dict[str, Any] | None = None
        self.waiting: list[str] = []
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT)
        self.merges: list[MergeJob] = []
        self._merge_ids = 0
        self.next_poll: float | None = None

    def _changed(self) -> None:
        self.on_change()

    # ── the loop reports ──

    def set_state(self, state: str, detail: str = "") -> None:
        with self._lock:
            if (self.state, self.detail) == (state, detail):
                return
            self.state, self.detail = state, detail
            if state != "encoding":
                self.current = None
        self._changed()

    def begin_batch(self, files: list[str]) -> None:
        with self._lock:
            self.state, self.detail = "encoding", ""
            self.batch_total, self.batch_done = len(files), 0
            self.waiting = list(files)
        self._changed()

    def begin_file(self, name: str) -> None:
        with self._lock:
            if name in self.waiting:
                self.waiting.remove(name)
            self.current = {"file": name, "step": "checking", "preset": "", "encoder": "",
                            "percent": 0.0, "eta": None, "started": time.time()}
        self._changed()

    def encoding(self, preset: str, encoder: str) -> None:
        with self._lock:
            if self.current is not None:
                self.current.update(step="encoding", preset=preset, encoder=encoder,
                                    percent=0.0, eta=None, started=time.time())
        self._changed()

    def progress(self, percent: float) -> None:
        with self._lock:
            cur = self.current
            if cur is None:
                return
            percent = max(0.0, min(percent, 100.0))
            elapsed = time.time() - cur["started"]
            cur["percent"] = percent
            cur["eta"] = round(elapsed * (100 - percent) / percent) if percent >= 1 else None
        self._changed()

    def end_file(self, result: dict[str, Any]) -> None:
        with self._lock:
            self.batch_done += 1
            self.current = None
            self.recent.appendleft({
                "file": result.get("file", ""), "status": result.get("status", ""),
                "reason": result.get("reason", ""), "path": result.get("path", ""),
                "original_size": result.get("original_size", 0),
                "compressed_size": result.get("compressed_size", 0),
                "at": time.time(),
            })
        self._changed()

    def end_batch(self) -> None:
        with self._lock:
            self.waiting = []
            self.current = None
        self._changed()

    def sleep(self, seconds: float) -> None:
        """Wait for the next poll; a resume or stop from the page ends the wait early."""
        with self._lock:
            self.next_poll = time.time() + seconds
        self._changed()
        self._wake.wait(seconds)
        self._wake.clear()
        with self._lock:
            self.next_poll = None

    # ── merges ──

    def queue_merge(self, paths: list[str], stem: str, ext: str) -> MergeJob:
        with self._lock:
            busy = {p for m in self.merges if m.state in ("queued", "running") for p in m.paths}
            if busy & set(paths):
                raise ValueError("one of those videos is already being merged")
            self._merge_ids += 1
            job = MergeJob(self._merge_ids, list(paths), stem, ext)
            self.merges.append(job)
        self._wake.set()            # don't wait for the next poll
        self._changed()
        return job

    def next_merge(self) -> MergeJob | None:
        with self._lock:
            return next((m for m in self.merges if m.state == "queued"), None)

    def merge(self, job_id: int) -> MergeJob:
        with self._lock:
            for m in self.merges:
                if m.id == job_id:
                    return m
        raise KeyError(job_id)

    def update_merge(self, job: MergeJob, state: str | None = None, detail: str | None = None,
                     output: str | None = None) -> None:
        with self._lock:
            if state is not None:
                job.state = state
            if detail is not None:
                job.detail = detail
            if output is not None:
                job.output = output
        self._changed()

    def forget_merge(self, job_id: int) -> None:
        with self._lock:
            self.merges = [m for m in self.merges
                           if m.id != job_id or m.state in ("queued", "running")]
        self._changed()

    # ── the page asks ──

    def pause(self) -> None:
        """Stop after the current file; the rest stay in the root for later."""
        with self._lock:
            self.paused = True
        self._changed()

    def stop_now(self) -> None:
        """Pause, and stop the current encode too; that file stays in the root, untouched."""
        with self._lock:
            self.paused = True
            self.cancel.set()
        self._changed()

    def resume(self) -> None:
        with self._lock:
            self.paused = False
            self.cancel.clear()
        self._wake.set()
        self._changed()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state, "detail": self.detail, "paused": self.paused,
                "batch_total": self.batch_total, "batch_done": self.batch_done,
                "current": dict(self.current) if self.current else None,
                "waiting": list(self.waiting), "recent": list(self.recent),
                "next_poll": self.next_poll, "settings": dict(self.settings),
                "merges": [m.to_json() for m in self.merges],
            }

    @property
    def busy(self) -> bool:
        return self.state in ("encoding", "merging")


class KeepAwake:
    """Hold a macOS `caffeinate -i` assertion while vidlib has work, so a MacBook doesn't
    idle-sleep mid-download or mid-encode. `-w` ties it to this process, so it can never
    outlive vidlib. Does nothing elsewhere, or without caffeinate."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self.available = sys.platform == "darwin" and shutil.which("caffeinate") is not None

    def set(self, busy: bool) -> None:
        if not self.available:
            return
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            if busy and not running:
                self._proc = subprocess.Popen(
                    ["caffeinate", "-i", "-w", str(os.getpid())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
            elif not busy and running:
                assert self._proc is not None
                self._proc.terminate()
                self._proc = None

    def close(self) -> None:
        self.set(False)
