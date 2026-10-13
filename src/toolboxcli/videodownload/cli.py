"""
video-download — Download every video listed in ./urls.txt using yt-dlp + aria2c.

Requires:
    yt-dlp, aria2c

Usage:
    video-download              # download one at a time
    video-download -j 3         # run up to 3 downloads in parallel

urls.txt is read from the current folder, one download per line: a URL, then
optionally a space and the output file name. The name may contain spaces and
takes no extension (yt-dlp adds the right one); without a name, the video's
title is used. Blank lines and lines starting with # are ignored.

    https://example.com/watch?v=abc Trip to the Lakes - Day 1
    https://example.com/watch?v=def

Each line runs:
    yt-dlp --external-downloader aria2c --external-downloader-args "-x 16 -k 1M"
           -o "<name or %(title)s>.%(ext)s" <url>

With -j 1, yt-dlp's own output is shown as-is. With more, each running
download gets one status line showing its latest output, and a failed
download's last lines of output are printed when it finishes. Every aria2c
opens 16 connections, so -j 4 means up to 64 at once.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from toolboxcli._common.console import console, die, error, info, ok, warn
from toolboxcli._common.progress import spinner
from toolboxcli._common.tooling import require_tool

URLS_FILE = "urls.txt"
ARIA2C_ARGS = "-x 16 -k 1M"
FAIL_TAIL_LINES = 15
STATUS_WIDTH = 70


@dataclass(frozen=True)
class Download:
    line_no: int
    url: str
    name: str | None  # None → let yt-dlp name it after the video title

    @property
    def label(self) -> str:
        return self.name or self.url


def parse_urls_file(path: Path) -> list[Download]:
    """Parse `<url>[ <name with spaces>]` lines. utf-8-sig tolerates a Notepad BOM."""
    downloads = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # A URL never contains whitespace, so everything after the first run of
        # whitespace is the name — spaces inside the name are kept verbatim.
        parts = line.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else None
        downloads.append(Download(line_no, parts[0], name or None))
    return downloads


def output_template(name: str | None) -> str:
    if name is None:
        return "%(title)s.%(ext)s"
    # -o is a yt-dlp template: a literal % in a user-supplied name must be
    # doubled, or "100% Done" would be read as a (broken) template field.
    return name.replace("%", "%%") + ".%(ext)s"


def build_command(dl: Download) -> list[str]:
    return [
        "yt-dlp",
        "--external-downloader", "aria2c",
        "--external-downloader-args", ARIA2C_ARGS,
        "-o", output_template(dl.name),
        dl.url,
    ]


def run_sequential(downloads: list[Download]) -> list[Download]:
    failed = []
    total = len(downloads)
    for i, dl in enumerate(downloads, start=1):
        info(f"[{i}/{total}] {escape(dl.label)}")
        result = subprocess.run(build_command(dl))
        if result.returncode == 0:
            ok(f"Downloaded {escape(dl.label)}")
        else:
            error(f"yt-dlp exited with code {result.returncode} for line {dl.line_no}: {escape(dl.url)}")
            failed.append(dl)
    return failed


def _shorten(text: str, width: int = STATUS_WIDTH) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def run_parallel(downloads: list[Download], workers: int) -> list[Download]:
    failed = []
    running: set[subprocess.Popen] = set()
    lock = threading.Lock()

    with spinner() as progress:

        def worker(dl: Download) -> tuple[int, list[str]]:
            label = escape(_shorten(dl.label, 40))
            task = progress.add_task(label, total=None)
            tail: deque[str] = deque(maxlen=FAIL_TAIL_LINES)
            try:
                proc = subprocess.Popen(
                    build_command(dl),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,  # universal newlines also splits aria2c's \r progress redraws
                    encoding="utf-8",
                    errors="replace",
                )
                with lock:
                    running.add(proc)
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    tail.append(line)
                    progress.update(task, description=f"{label} [dim]{escape(_shorten(line))}[/dim]")
                returncode = proc.wait()
                with lock:
                    running.discard(proc)
                return returncode, list(tail)
            finally:
                progress.remove_task(task)

        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {pool.submit(worker, dl): dl for dl in downloads}
            for future in as_completed(futures):
                dl = futures[future]
                try:
                    returncode, tail = future.result()
                except Exception as exc:  # e.g. OSError launching yt-dlp
                    returncode, tail = -1, [str(exc)]
                if returncode == 0:
                    ok(f"Downloaded {escape(dl.label)}")
                    continue
                failed.append(dl)
                # Printed through the shared stdout console rather than error(),
                # which writes to stderr and would tear the live progress display.
                console.print(
                    f"[red][ERROR][/red] yt-dlp exited with code {returncode} "
                    f"for line {dl.line_no}: {escape(dl.url)}"
                )
                for line in tail:
                    console.print(f"    [dim]{escape(line)}[/dim]")
        except KeyboardInterrupt:
            # Don't start anything still queued, and make sure no yt-dlp is left
            # running in the background (Ctrl+C doesn't always reach children on
            # Windows).
            pool.shutdown(wait=False, cancel_futures=True)
            with lock:
                for proc in running:
                    proc.terminate()
            raise
        pool.shutdown(wait=True)

    return failed


def positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-download",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-j", "--jobs", metavar="N", type=positive_int, default=1,
                        help="Number of downloads to run in parallel (default: 1)")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    urls_path = Path.cwd() / URLS_FILE
    if not urls_path.is_file():
        die(f"no {URLS_FILE} found in {Path.cwd()}")

    downloads = parse_urls_file(urls_path)
    if not downloads:
        warn(f"{URLS_FILE} has no URLs in it — nothing to do")
        return

    require_tool("yt-dlp")
    require_tool("aria2c")

    workers = min(args.jobs, len(downloads))
    info(f"{len(downloads)} download(s) from {URLS_FILE}, {workers} at a time")

    try:
        if workers == 1:
            failed = run_sequential(downloads)
        else:
            failed = run_parallel(downloads, workers)
    except KeyboardInterrupt:
        die("interrupted", code=130)

    succeeded = len(downloads) - len(failed)
    if not failed:
        ok(f"All {succeeded} download(s) finished")
        return

    warn(f"{succeeded} succeeded, {len(failed)} failed:")
    for dl in failed:
        console.print(f"    line {dl.line_no}: {escape(dl.url)}")
    sys.exit(1)


if __name__ == "__main__":
    main()
