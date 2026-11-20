"""The download engine shared by video-download's batch mode and its TUI.

Runs yt-dlp (with aria2c doing the transfer) in worker threads, at most `workers` at a time,
and reports through callbacks rather than printing — so the same engine can drive a rich
status line or a Textual widget. Every output line is passed on; lines that carry progress
are parsed into a core.Progress first.

Callbacks run on worker threads. A UI must hand them to its own thread without blocking —
Textual's thread-safe `post_message`, not `call_from_thread`, which waits on the UI thread and
can deadlock against a shutdown. A plain rich Progress is thread-safe and can be updated
directly.
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from toolboxcli.videodownload.core import Progress, parse_progress

ARIA2C_ARGS = "-x 16 -k 1M"
TAIL_LINES = 15


def output_template(name: str | None) -> str:
    if not name:
        return "%(title)s.%(ext)s"
    # -o is a yt-dlp template: a literal % in a user-supplied name must be doubled, or
    # "100% Done" would be read as a (broken) template field.
    return name.replace("%", "%%") + ".%(ext)s"


def build_command(url: str, name: str | None, newline: bool = True) -> list[str]:
    """yt-dlp's command. `newline` makes it print one progress line per update, which the
    engine parses; without it (yt-dlp writing straight to a terminal) it redraws in place."""
    return [
        "yt-dlp",
        *(["--newline"] if newline else []),
        "--external-downloader", "aria2c",
        "--external-downloader-args", ARIA2C_ARGS,
        "-o", output_template(name),
        url,
    ]


@dataclass
class Job:
    """One download. `id` is the caller's: an index, a queue row, anything hashable."""

    id: object
    url: str
    name: str | None = None
    # Asked just before the job starts; False skips it (marked cancelled, never run). Lets a
    # caller drop a job whose reason has gone away while it waited in the queue.
    still_wanted: Callable[[], bool] | None = None
    returncode: int | None = None
    cancelled: bool = False
    tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))


OnLine = Callable[[Job, str, Progress | None], None]
OnStart = Callable[[Job], None]
OnDone = Callable[[Job], None]


def _noop(*_args: object) -> None:
    pass


class Engine:
    """Run jobs `workers` at a time. Use as a context manager; leaving it cancels anything
    still running, so no yt-dlp is ever left behind."""

    def __init__(self, workers: int, on_line: OnLine = _noop, on_start: OnStart = _noop,
                 on_done: OnDone = _noop) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers))
        self._lock = threading.Lock()
        self._running: dict[int, subprocess.Popen[str]] = {}   # id(job) -> process
        self._jobs: dict[int, Job] = {}
        self.on_line, self.on_start, self.on_done = on_line, on_start, on_done

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown(cancel=exc[0] is not None)

    def submit(self, job: Job) -> Future[Job]:
        with self._lock:
            self._jobs[id(job)] = job
        return self._pool.submit(self._run, job)

    def cancel(self, job: Job) -> None:
        """Stop a job: a running one is terminated, a queued one never starts."""
        with self._lock:
            job.cancelled = True
            proc = self._running.get(id(job))
        if proc is not None:
            proc.terminate()

    def shutdown(self, cancel: bool = False, wait: bool = True) -> None:
        """Stop taking jobs. With `cancel`, stop the running ones and drop the queued ones.
        A UI quitting passes wait=False: its worker threads finish on their own as soon as
        the terminated yt-dlp processes exit, without blocking the UI's event loop."""
        if cancel:
            with self._lock:
                jobs = list(self._jobs.values())
            for job in jobs:
                self.cancel(job)
        self._pool.shutdown(wait=wait, cancel_futures=cancel)

    def _run(self, job: Job) -> Job:
        if not job.cancelled and job.still_wanted is not None and not job.still_wanted():
            job.cancelled = True
        if job.cancelled:
            return job
        try:
            proc = subprocess.Popen(
                build_command(job.url, job.name),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",  # also splits aria2c's \r
            )
        except OSError as exc:
            job.returncode = -1
            job.tail.append(str(exc))
            self.on_done(job)
            return job
        with self._lock:
            self._running[id(job)] = proc
        self.on_start(job)
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if line:
                    job.tail.append(line)
                    self.on_line(job, line, parse_progress(line))
            job.returncode = proc.wait()
        finally:
            with self._lock:
                self._running.pop(id(job), None)
                self._jobs.pop(id(job), None)
        self.on_done(job)
        return job
