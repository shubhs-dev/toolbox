"""
video-download — Download videos with yt-dlp + aria2c, named People - Title - Collection.

Requires:
    yt-dlp, aria2c

Usage:
    video-download                    # open the downloader (a terminal UI)
    video-download -L ~/Library       # …suggesting people/collections from that library
    video-download -b                 # batch: download everything in ./urls.txt
    video-download -b -j 3            # batch, up to 3 downloads in parallel
    video-download -b -y              # batch, also download lines added mid-run

THE DOWNLOADER (default)

Paste a URL and the page's title. The title is split into words, and each word is
labelled people, title or collection — guessed from names the library (-L, or env
VIDEO_DOWNLOAD_LIBRARY: an vidlib library root) and earlier downloads already use,
everything after the last "|" skipped as the site name. Correct the guess in the word
bar: ←/→ move, shift+←/→ select, p / t / c toggle people / title / collection, x skips.
The People, Title and Collection fields it fills in can also be edited directly. ctrl+s
queues the download, named "People - Title - Collection"; downloads start straight away,
-j at a time (default 2), into the current folder.

In the queue: r retries a failed or cancelled download, d cancels or removes one, D clears
finished ones. ctrl+q quits; anything unfinished resumes next time, from
.video-download-queue.json in the folder.

BATCH (-b)

urls.txt is read from the current folder, one download per line: a URL, then
optionally a space and the output file name. The name may contain spaces and
takes no extension (yt-dlp adds the right one); without a name, the video's
title is used. Blank lines and lines starting with # are ignored.

    https://example.com/watch?v=abc Trip to the Lakes - Day 1
    https://example.com/watch?v=def

Each line runs:
    yt-dlp --external-downloader aria2c --external-downloader-args "-x 16 -k 1M"
           -o "<name or %(title)s>.%(ext)s" <url>

urls.txt is kept up to date as the run goes: once a download completes
successfully its line is removed from the file. The line is found again by its
URL at that moment, so editing the file mid-run (adding lines, deleting ones you
no longer want) is safe — a line you delete before its download starts is
skipped. Failed or interrupted downloads keep their line for the next run.

Before finishing, urls.txt is read again; if lines were added during the run,
you're asked whether to download those too (-y answers yes).

With -j 1, yt-dlp's own output is shown as-is. With more, each running
download gets one status line showing its progress (percent, speed, time
left), and a failed
download's last lines of output are printed when it finishes. Every aria2c
opens 16 connections, so -j 4 means up to 64 at once.
"""

from __future__ import annotations

import argparse
import codecs
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import as_completed
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape
from rich.progress import TaskID

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, die, error, info, ok, warn
from toolboxcli._common.progress import spinner
from toolboxcli._common.tooling import require_tool
from toolboxcli.videodownload import engine
from toolboxcli.videodownload.core import KnownNames, Progress
from toolboxcli.vidlib.db import DB_NAME, LEGACY_DB_NAME

URLS_FILE = "urls.txt"
ARIA2C_ARGS = "-x 16 -k 1M"
FAIL_TAIL_LINES = 15
STATUS_WIDTH = 70


@dataclass(frozen=True)
class Download:
    url: str
    name: str | None  # None → let yt-dlp name it after the video title

    @property
    def label(self) -> str:
        return self.name or self.url


def parse_line(raw: str) -> Download | None:
    """Parse one `<url>[ <name with spaces>]` line; None for blanks and comments."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    # A URL never contains whitespace, so everything after the first run of
    # whitespace is the name — spaces inside the name are kept verbatim.
    parts = line.split(maxsplit=1)
    name = parts[1].strip() if len(parts) > 1 else None
    return Download(parts[0], name or None)


class UrlsFile:
    """urls.txt, re-read on every access since the user may edit it mid-run.

    Lines are always located by URL rather than by line number, so additions and
    deletions made while downloads are running don't throw the bookkeeping off.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()  # parallel workers finish concurrently

    def _read(self) -> tuple[bool, list[str]]:
        """(had a BOM, lines with their own line endings). Missing file → no lines."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return False, []
        # utf-8-sig tolerates a Notepad BOM; it's remembered so a rewrite keeps it.
        return raw.startswith(codecs.BOM_UTF8), raw.decode("utf-8-sig").splitlines(keepends=True)

    def downloads(self) -> list[Download]:
        with self._lock:
            _, lines = self._read()
        return [dl for dl in map(parse_line, lines) if dl is not None]

    def _find(self, lines: list[str], dl: Download) -> int | None:
        """Index of dl's line: an exact URL + name match first, else the first line with its URL."""
        fallback = None
        for i, line in enumerate(lines):
            entry = parse_line(line)
            if entry is None or entry.url != dl.url:
                continue
            if entry == dl:
                return i
            if fallback is None:
                fallback = i
        return fallback

    def contains(self, dl: Download) -> bool:
        with self._lock:
            _, lines = self._read()
            return self._find(lines, dl) is not None

    def remove(self, dl: Download) -> bool:
        """Drop dl's line. False if it's already gone (deleted by hand) or the write failed."""
        with self._lock:
            bom, lines = self._read()
            index = self._find(lines, dl)
            if index is None:
                return False
            del lines[index]
            data = "".join(lines).encode("utf-8")
            if bom:
                data = codecs.BOM_UTF8 + data
            # Write a sibling temp file and swap it in, so a crash mid-write can
            # never leave a truncated urls.txt behind.
            tmp = self.path.with_name(f".{self.path.name}.tmp")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, self.path)
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                warn(f"couldn't remove {escape(dl.url)} from {self.path.name}: {exc}")
                return False
            return True


def output_template(name: str | None) -> str:
    return engine.output_template(name)


def build_command(dl: Download, newline: bool = True) -> list[str]:
    return engine.build_command(dl.url, dl.name, newline=newline)


@dataclass
class Results:
    succeeded: int = 0
    skipped: int = 0
    failed: list[Download] = field(default_factory=list)


def record_success(urls: UrlsFile, dl: Download, results: Results) -> None:
    """Called only once yt-dlp has exited 0, i.e. the file is complete on disk."""
    results.succeeded += 1
    if urls.remove(dl):
        ok(f"Downloaded {escape(dl.label)} — removed from {urls.path.name}")
    else:
        ok(f"Downloaded {escape(dl.label)}")


def run_sequential(downloads: list[Download], urls: UrlsFile, results: Results) -> None:
    total = len(downloads)
    for i, dl in enumerate(downloads, start=1):
        if not urls.contains(dl):
            info(f"[{i}/{total}] {escape(dl.label)} — no longer in {urls.path.name}, skipping")
            results.skipped += 1
            continue
        info(f"[{i}/{total}] {escape(dl.label)}")
        result = subprocess.run(build_command(dl, newline=False))
        if result.returncode == 0:
            record_success(urls, dl, results)
        else:
            error(f"yt-dlp exited with code {result.returncode} for {escape(dl.url)}")
            results.failed.append(dl)


def _shorten(text: str, width: int = STATUS_WIDTH) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _status(line: str, progress: Progress | None) -> str:
    """What a running download's status line shows: its progress when the line carried
    some, else the line itself."""
    if progress is None:
        return _shorten(line)
    parts = [f"{progress.percent:5.1f}%"]
    if progress.speed:
        parts.append(progress.speed)
    if progress.eta:
        parts.append(f"ETA {progress.eta}")
    return "  ".join(parts)


def run_parallel(downloads: list[Download], workers: int, urls: UrlsFile, results: Results) -> None:
    with spinner() as progress:
        tasks: dict[int, TaskID] = {}

        def on_line(job: engine.Job, line: str, prog: Progress | None) -> None:
            assert isinstance(job.id, int)
            if job.id in tasks:
                label = escape(_shorten(downloads[job.id].label, 40))
                progress.update(tasks[job.id],
                                description=f"{label} [dim]{escape(_status(line, prog))}[/dim]")

        def on_start(job: engine.Job) -> None:
            assert isinstance(job.id, int)
            tasks[job.id] = progress.add_task(escape(_shorten(downloads[job.id].label, 40)),
                                              total=None)

        def on_done(job: engine.Job) -> None:
            assert isinstance(job.id, int)
            task = tasks.pop(job.id, None)
            if task is not None:
                progress.remove_task(task)

        with engine.Engine(workers, on_line=on_line, on_start=on_start, on_done=on_done) as eng:
            futures = {
                # A line deleted from urls.txt before its turn is never started.
                eng.submit(engine.Job(id=index, url=dl.url, name=dl.name,
                                      still_wanted=lambda dl=dl: urls.contains(dl))): dl
                for index, dl in enumerate(downloads)
            }
            for future in as_completed(futures):
                dl, job = futures[future], future.result()
                if job.cancelled and job.returncode is None:
                    results.skipped += 1
                    info(f"{escape(dl.label)} — no longer in {urls.path.name}, skipping")
                elif job.returncode == 0:
                    record_success(urls, dl, results)
                else:
                    results.failed.append(dl)
                    # Printed through the shared stdout console rather than error(), which
                    # writes to stderr and would tear the live progress display.
                    console.print(f"[red][ERROR][/red] yt-dlp exited with code "
                                  f"{job.returncode} for {escape(dl.url)}")
                    for line in job.tail:
                        console.print(f"    [dim]{escape(line)}[/dim]")


def positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-download",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-b", "--batch", action="store_true",
                        help="Download everything in ./urls.txt instead of opening the UI")
    parser.add_argument("-j", "--jobs", metavar="N", type=positive_int, default=None,
                        help="Number of downloads to run in parallel (default: 1 in batch "
                             "mode, 2 in the UI)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Batch: download lines added to urls.txt during the run "
                             "without asking")
    parser.add_argument("-L", "--library", metavar="DIR", default=None,
                        help="vidlib library root whose people and collections the UI "
                             "suggests (default: env VIDEO_DOWNLOAD_LIBRARY)")
    return parser


def library_names(library: str | None) -> KnownNames:
    """People and collections from an vidlib library's database, read-only — never
    created, migrated or written. Missing or unreadable means no suggestions, not an error."""
    folder = library or os.environ.get("VIDEO_DOWNLOAD_LIBRARY")
    if not folder:
        return KnownNames()
    path = Path(folder).expanduser() / DB_NAME
    if not path.is_file() and path.with_name(LEGACY_DB_NAME).is_file():
        path = path.with_name(LEGACY_DB_NAME)   # vidlib hasn't renamed it yet
    if not path.is_file():
        warn(f"No vidlib database at {path} — no name suggestions from the library")
        return KnownNames()
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            people = {r[0] for r in conn.execute("SELECT key FROM people")}
            collections = {r[0] for r in conn.execute(
                "SELECT DISTINCT collection_key FROM videos WHERE collection_key != ''")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        warn(f"Couldn't read {path}: {exc} — no name suggestions from the library")
        return KnownNames()
    return KnownNames(people, collections)


def run_tui(args: argparse.Namespace) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        die("the downloader needs a terminal — use -b to download ./urls.txt instead")
    require_tool("yt-dlp")
    require_tool("aria2c")
    from toolboxcli.videodownload.tui import DownloadApp  # Textual loads only when needed

    DownloadApp(Path.cwd(), workers=args.jobs or 2, known=library_names(args.library)).run()


def main() -> None:
    args = build_parser().parse_args()
    if not args.batch:
        run_tui(args)
        return
    run_batch(args)


def run_batch(args: argparse.Namespace) -> None:
    urls_path = Path.cwd() / URLS_FILE
    if not urls_path.is_file():
        die(f"no {URLS_FILE} found in {Path.cwd()}")

    urls = UrlsFile(urls_path)
    downloads = urls.downloads()
    if not downloads:
        warn(f"{URLS_FILE} has no URLs in it — nothing to do")
        return

    require_tool("yt-dlp")
    require_tool("aria2c")

    results = Results()
    # Everything tried this run, so a failed line still sitting in urls.txt isn't
    # mistaken for a newly added one (and retried in a loop) at the end-of-run check.
    attempted: set[Download] = set()

    while downloads:
        attempted.update(downloads)
        workers = min(args.jobs or 1, len(downloads))
        info(f"{len(downloads)} download(s) from {URLS_FILE}, {workers} at a time")

        try:
            if workers == 1:
                run_sequential(downloads, urls, results)
            else:
                run_parallel(downloads, workers, urls, results)
        except KeyboardInterrupt:
            die("interrupted", code=130)

        downloads = [dl for dl in urls.downloads() if dl not in attempted]
        if not downloads:
            break
        info(f"{len(downloads)} new line(s) were added to {URLS_FILE} during the run")
        if not args.yes and confirm("Download them too?", default="y") != "y":
            break

    summary = f"{results.succeeded} succeeded"
    if results.skipped:
        summary += f", {results.skipped} skipped (removed from {URLS_FILE})"
    if not results.failed:
        ok(f"All done: {summary}")
        return

    warn(f"{summary}, {len(results.failed)} failed (still in {URLS_FILE}):")
    for dl in results.failed:
        console.print(f"    {escape(dl.url)}")
    sys.exit(1)


if __name__ == "__main__":
    main()
