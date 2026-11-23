"""Runs yt-dlp downloads for vidlib's web server.

yt-dlp (with aria2c doing the transfer) runs in worker threads, at most `workers` at a time,
and reports through callbacks rather than printing. Every output line is passed on; lines
that carry progress are parsed into a Progress first. Callbacks run on worker threads, so
whatever they touch must be thread-safe (the download manager takes its lock).

Each job writes into its own folder (`Job.folder`), so the finished file is whatever is left
there that isn't a partial — no need to predict the extension yt-dlp picks, or the title it
names an unnamed download after.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from toolboxcli._common.tooling import detached_kwargs, terminate_tree

# yt-dlp runs aria2c with its live readout on and its summary off. Into a pipe the readout is
# buffered until aria2c exits, so the page saw 0% and then 100%. A one-line summary every
# second is flushed as it goes, and _ARIA2C_RE reads its "[#id 3MiB/9MiB(30%) … DL:…]" line.
# These come after yt-dlp's own aria2c options, so they win.
ARIA2C_ARGS = "aria2c:-x 16 -k 1M --summary-interval=1 --show-console-readout=false"
# The rest of each summary: banner, rules and the file name. Kept out of a job's tail, whose
# last line is shown as the reason a download failed.
_SUMMARY_NOISE = re.compile(r"^(?:\*\*\* Download Progress Summary|[=-]{20,}$|FILE: )")
TAIL_LINES = 15


# What yt-dlp and aria2c leave behind mid-download; never mistaken for the finished file.
PARTIAL_SUFFIXES = (".part", ".ytdl", ".aria2", ".temp", ".tmp")


# ── Progress lines ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Progress:
    """Where a running download is, as read from one line of its output."""

    percent: float
    speed: str = ""     # as the tool printed it: "4.2MiB/s"
    eta: str = ""       # "00:05", "4m50s"


# yt-dlp's own downloader (with --newline):
#   [download]  45.3% of ~  10.00MiB at    1.23MiB/s ETA 00:05 (frag 3/10)
_YTDLP_RE = re.compile(
    r"\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%"
    r"(?:.*?\bat\s+(?P<speed>\S+/s))?(?:.*?\bETA\s+(?P<eta>[\d:]+))?"
)
# aria2c, which yt-dlp hands the download to here:
#   [#2089b0 400.0KiB/33.2MiB(1%) CN:16 DL:4.2MiB ETA:4m50s]
_ARIA2C_RE = re.compile(
    r"\[#\w+\s+\S+?\((?P<pct>\d+(?:\.\d+)?)%\)"
    r"(?:.*?\bDL:(?P<speed>[\d.]+\w*))?(?:.*?\bETA:(?P<eta>\w+))?"
)


def parse_progress(line: str) -> Progress | None:
    """A Progress from a yt-dlp or aria2c status line, or None for any other line."""
    for pattern, per_second in ((_YTDLP_RE, ""), (_ARIA2C_RE, "/s")):
        m = pattern.search(line)
        if m:
            speed = m.group("speed") or ""
            return Progress(
                percent=min(float(m.group("pct")), 100.0),
                speed=f"{speed}{per_second}" if speed and per_second else speed,
                eta=m.group("eta") or "",
            )
    return None


_UNITS = {"": 1, "B": 1, "K": 1e3, "M": 1e6, "G": 1e9, "KI": 1024, "MI": 1024**2, "GI": 1024**3}
_RATE_RE = re.compile(r"^([\d.]+)\s*([KMG]?I?)B?/S$")


def rate_bytes(speed: str) -> float:
    """A printed speed ("4.2MiB/s", "1.5MB/s", "800KiB/s") in bytes per second; 0 if unread."""
    m = _RATE_RE.match(speed.strip().upper())
    if not m or m.group(2) not in _UNITS:
        return 0.0
    try:
        return float(m.group(1)) * _UNITS[m.group(2)]
    except ValueError:
        return 0.0


# ── Commands ─────────────────────────────────────────────────────────


def _template_text(text: str) -> str:
    # -o is a yt-dlp template: a literal % must be doubled, or "100% Done" (or a folder
    # with a % in it) would be read as a broken template field.
    return text.replace("%", "%%")


def output_template(folder: Path, name: str | None) -> str:
    stem = _template_text(name) if name else "%(title)s"
    return f"{_template_text(str(folder))}/{stem}.%(ext)s"


def build_command(url: str, folder: Path, name: str | None) -> list[str]:
    """yt-dlp's command; --newline makes it print one progress line per update."""
    return [
        "yt-dlp",
        "--newline",
        "--external-downloader", "aria2c",
        "--external-downloader-args", ARIA2C_ARGS,
        "-o", output_template(folder, name),
        url,
    ]


def fetch_title(url: str, timeout: float = 120) -> str:
    """The page's title, as yt-dlp reads it, without downloading anything. Raises
    RuntimeError with yt-dlp's last line of output when it can't."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--skip-download", "--no-warnings", "--no-playlist",
             "--print", "title", url],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(str(exc)) from exc
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if result.returncode != 0 or not lines:
        errors = [ln.strip() for ln in result.stderr.splitlines() if ln.strip()]
        raise RuntimeError(errors[-1] if errors else f"yt-dlp exited {result.returncode}")
    return lines[0]


def finished_files(folder: Path) -> list[Path]:
    """What a job left in its folder, partials excluded."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and not p.name.lower().endswith(PARTIAL_SUFFIXES))


@dataclass
class Job:
    """One download. `id` is the caller's: an index, a queue row, anything hashable."""

    id: object
    url: str
    folder: Path
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
            terminate_tree(proc)

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
            with self._lock:
                self._jobs.pop(id(job), None)
            self.on_done(job)   # a job cancelled while it waited still reports its end
            return job
        try:
            # Never parents=True: if the disk holding the target folder has been unplugged,
            # creating the path would put a stray folder where its mount point was (on macOS
            # the disk then comes back as "Name 1"). Only _incoming/ and the job folder are
            # ever created, so a missing target fails the job instead.
            job.folder.parent.mkdir(exist_ok=True)
            job.folder.mkdir(exist_ok=True)
            # Detached (its own session on POSIX) so a cancel can stop aria2c too: yt-dlp
            # runs it as a child writing into this same pipe, and outlives a kill of yt-dlp.
            proc = subprocess.Popen(
                build_command(job.url, job.folder, job.name),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",  # also splits aria2c's \r
                **detached_kwargs(),
            )
        except OSError as exc:
            job.returncode = -1
            job.tail.append(str(exc))
            self.on_done(job)
            return job
        with self._lock:
            self._running[id(job)] = proc
            if job.cancelled:        # cancelled between the check above and Popen
                terminate_tree(proc)
        self.on_start(job)
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if line and not _SUMMARY_NOISE.match(line):
                    job.tail.append(line)
                    self.on_line(job, line, parse_progress(line))
            job.returncode = proc.wait()
        finally:
            with self._lock:
                self._running.pop(id(job), None)
                self._jobs.pop(id(job), None)
        self.on_done(job)
        return job
