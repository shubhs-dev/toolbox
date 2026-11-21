"""
vidlib — A video library: download new videos, optimise them to 1080p, tag and sort them.

Runs continuously next to the library. While it runs, a web page at http://127.0.0.1:8642/
(opened in your browser on start) downloads videos into the library: paste a URL and the
page's title, mark which parts of the title are the people, the title and the collection,
and the download is named "People - Title - Collection" and dropped into the root, where the
pipeline below picks it up.

For every video that appears directly in the library root it will:

  1. Transcode it with HandBrake — the 720p preset for <=720p sources, the 1080p preset
     otherwise. Neither preset upscales. Every audio track is kept, encoded to stereo AAC
     at 160k. The result is kept only when it is smaller than the original; the original
     goes to the Recycle Bin, never deleted outright.
  2. Rewrite the filename to carry a resolution tag, so 720p files you may want to upscale
     later with a better tool are visible at a glance:
         People - Title - Trip  ->  People - Title - Trip [1080p].mp4
     Tags you added yourself are preserved: [Restored 720p] -> [Restored 1080p].
  3. Compare it against any copy already in the library — the same people, title and
     collection, in any order or case and whatever its tags, in any folder. A
     higher-resolution or more complete version replaces the old one (which goes to the
     Recycle Bin); anything ambiguous is parked in _review/ for you to decide.
  4. Move it into the folder named by its Trip field — the last " - " separated part of
     the name.

Only files sitting directly in the root are touched. Subfolders — including the trip
folders and any folder you use to stage files by hand — are left completely alone.
_incoming/ is skipped entirely: it holds downloads that are still being written.

While a batch runs, an overall progress bar is pinned beneath the current file's: files
done and left, a tally of outcomes (done / review / skipped / failed), elapsed time and ETA.

On every poll it also looks for subtitle files sitting in the root. A subtitle is matched to
a video by the first " - " field of its name (e.g. "People" in "People - Title - Trip") —
only an unambiguous 1:1 match is merged; anything ambiguous is left alone and retried next
poll. A match is merged in with `addsub` and the file is tagged [... Sub] instead of getting
an addsub-style " - Sub" filename suffix. This works both for a subtitle already sitting next
to a brand-new video, and for one dropped in later for a video that's already been optimised
and filed into a trip folder.

Everything vidlib knows lives in .vidlib.db, an SQLite file in the library root
(no server). Before each poll it syncs that with the disk: a file you renamed or moved by
hand keeps its history (so it's never re-encoded), files you add to folders yourself are
recorded as they are, and files that have gone are marked missing. An old .optimiselib.json
log is imported on the first run and kept as .optimiselib.json.migrated, and a library last
used as optimiselib has its .optimiselib.db renamed .vidlib.db. A dry run works on an
in-memory copy, so it changes nothing.

DOWNLOADS

A pasted title is split into segments on " - " and "|"; each gets a role, guessed from the
people and collections the library already has (also matching loosely, so "Doug-Doug"
suggests your "DougDoug"), then fixed by clicking. Any number of segments can be people or
title; one is the collection; a part after the last "|" (the site's name) is skipped.
Downloads run in _incoming/ and move into the root when they finish, -j at a time. They're
recorded in the library database, so the queue survives a restart.

The page's Library tab browses what the database knows — search, people, collection and
resolution filters, the duplicates, badly named files and the review queue — with a
thumbnail for each video (a few frames from across it on hover), and lets you play it, show it in Finder, rename it (moving it into
its new collection's folder; its history follows) or move it to the Trash. The Review tab
shows each file parked in _review/ next to the library copy it was compared with: keep the
new one (the library's goes to the Trash), keep the library's, or rename the new one to keep
both. A kept file goes back to the root, and the pipeline files it.

The page also shows the pipeline: the file being encoded (preset, encoder, progress, time
left), what's next, and what happened to recent files. Pause stops after the current file;
Stop now also stops its encode, leaving the file in the root as it was. While anything
downloads or encodes, the Mac is kept from idle-sleeping (caffeinate). If the library's disk
is unplugged, vidlib waits — nothing is synced, so nothing is marked missing — until it's back.

With -X DIR (download-only), nothing is encoded: downloads go into DIR (default: the current
folder) and wait there, recorded in ROOT's database, until "Move into library" on the page
moves them into ROOT. Useful while the library's disk is full.

Requires:
    HandBrakeCLI, ffprobe
    yt-dlp, aria2c (for downloads)
    addsub (optional — enables automatic subtitle merging when present)

Usage:
    vidlib /Volumes/Media/Library         # watch, poll every 30s, serve the web page
    vidlib -W                             # just the pipeline, no web page
    vidlib -X ~/Downloads/tmp             # download only, into that folder
    vidlib -P 9000 -b                     # web page on port 9000, don't open a browser
    vidlib -O                             # process what's there, then exit
    vidlib -n                             # dry run: show what would happen
    vidlib -r                             # report sub-1080p files and the review queue
    vidlib -l                             # list every video in the library, alphabetically
    vidlib -l library.txt                 # write that list to library.txt instead
    vidlib -a alice                       # everything with Alice in it
    vidlib -R 720p -c Lakes               # filters combine: 720p videos in Lakes
    vidlib -T Restored                    # by [tag]
    vidlib -U                             # files not named People - Title - Collection
    vidlib -D                             # videos the library holds more than once
    vidlib -H 01:00-07:00 -L 4            # only encode off-hours, and back off under load
    vidlib -S                             # don't scan for or merge subtitle files
    vidlib -s                             # merge waiting subtitles only, then exit
"""

from __future__ import annotations

import argparse
import errno
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rich import box
from rich.console import Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from toolboxcli._common import ffprobe as ffprobe_common
from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, die, info, ok, warn
from toolboxcli._common.handbrake import (
    available_encoders,
    load_preset,
    read_preset_settings,
    transcode,
    wait_stable,
)
from toolboxcli._common.humanize import human_size as fmt_size
from toolboxcli._common.progress import bar as progress_bar
from toolboxcli._common.tooling import detached_kwargs, require_tool
from toolboxcli._common.trash import move_to_trash
from toolboxcli.vidlib import core
from toolboxcli.vidlib.db import (
    INCOMING_DIR,
    LEGACY_DB_NAME,
    REVIEW_DIR,
    LibraryDB,
    Video,
    import_json_log,
    legacy_db_conflict,
)
from toolboxcli.vidlib.downloads import DownloadManager
from toolboxcli.vidlib.server import DEFAULT_PORT, VidlibServer
from toolboxcli.vidlib.status import KeepAwake, MergeJob, PipelineStatus

VIDEO_EXTS = core.VIDEO_EXTS

# Statuses meaning "this file is already compressed": vidlib wrote it, or recorded it
# as compressed (a sync of the library's folders, or the old log's reconciled files).
DONE_STATUSES = {"sorted", "compressed", "awaiting_trip", "skipped", "review"}


# ── Library database ──────────────────────────────────────────────────

def rel_path(root: Path, path: Path) -> str:
    """The database's key for a file: root-relative and '/'-separated on every platform."""
    return path.relative_to(root).as_posix()


def record(db: LibraryDB, root: Path, path: Path, status: str, **fields: object) -> None:
    """Write the row for the file now at *path*, reading its size and mtime from disk — the
    pair a later hand rename is recognised by."""
    try:
        st = path.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    db.upsert(Video(path=rel_path(root, path), status=status, size=size, mtime=mtime,
                    **fields))  # pyright: ignore[reportArgumentType]


def forget(db: LibraryDB, root: Path, *paths: Path) -> None:
    """Drop the rows of files that no longer exist under these names (renamed by the
    pipeline, or replaced by an encode with a different extension)."""
    for path in paths:
        if not path.exists():
            db.remove(rel_path(root, path))


def _skip_incoming(root: Path, dirpath: str, dirnames: list[str]) -> None:
    """Prune _incoming/ (downloads still being written) from an os.walk of the root."""
    if INCOMING_DIR in dirnames and Path(dirpath) == root:
        dirnames.remove(INCOMING_DIR)


def library_files(root: Path) -> dict[str, os.stat_result]:
    """Every video under *root*, _review/ included and _incoming/ not, keyed by rel_path."""
    found: dict[str, os.stat_result] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        _skip_incoming(root, dirpath, dirnames)
        for name in filenames:
            path = Path(dirpath) / name
            if _is_video(path):
                found[rel_path(root, path)] = path.stat()
    return found


def sync_library(root: Path, db: LibraryDB) -> None:
    """Bring the database in line with what's on disk, before each poll.

    - A file at a new path with the exact size and mtime of a row whose file is gone was
      renamed or moved by hand: its row (and so its history) follows it, so it's never
      mistaken for a new video and re-encoded.
    - A file in a folder (or _review/) that the database doesn't know is recorded as it is:
      vidlib never touches subfolders, so whatever is there is already compressed.
    - A file in the root it doesn't know is left alone: that's new input for the pipeline.
    - A row whose file has gone, and wasn't found elsewhere, is marked missing.
    """
    on_disk = library_files(root)
    known = {v.path: v for v in db.all(include_missing=True)}
    gone = [v for path, v in known.items() if path not in on_disk]

    unknown_in_folders: list[str] = []
    for path in sorted(p for p in on_disk if p not in known):
        st = on_disk[path]
        match = next((v for v in gone if v.size == st.st_size
                      and abs(v.mtime - st.st_mtime) < 0.001), None)
        if match is not None:
            gone.remove(match)
            db.move(match.path, path)
            db.log_event(path, "renamed", f"from {match.path}")
        elif "/" in path:
            unknown_in_folders.append(path)

    for path in on_disk:
        row = known.get(path)
        if row is not None and row.status == "missing":
            row.status = "sorted" if "/" in path else "compressed"
            db.upsert(row)
            db.log_event(path, "reappeared")

    for row in gone:
        if row.status != "missing":
            row.status = "missing"
            db.upsert(row)
            db.log_event(row.path, "missing")

    db.prune_people()   # people only a renamed-away name had mentioned

    if not unknown_in_folders:
        return
    with progress_bar() as progress:
        task = progress.add_task("Recording library files", total=len(unknown_in_folders))
        for path in unknown_in_folders:
            probed = probe(root / path)
            height, duration, depth, has_sub = probed or (0, 0.0, 8, False)
            status = "review" if path.startswith(f"{REVIEW_DIR}/") else "sorted"
            record(db, root, root / path, status, height=height, duration=duration,
                   bit_depth=depth, has_sub=has_sub, source_height=height)
            db.log_event(path, "found")
            progress.advance(task)
    info(f"Recorded {len(unknown_in_folders)} file(s) already in the library's folders")


# ── Batch progress display ────────────────────────────────────────────

# How each result status reads in the overall tally, in display order. dry-run is left out:
# every row of a dry run has it, so counting it says nothing.
_TALLY = (
    ("sorted", "[green]✓ {n} done[/]"),
    ("review", "[yellow]{n} to review[/]"),
    ("skipped", "[dim]{n} skipped[/]"),
    ("failed", "[red]✗ {n} failed[/]"),
)


class BatchDisplay:
    """The live display for one poll's batch: the current file's encode bar on top and an
    overall bar pinned beneath it — files done, files left, a tally of outcomes, elapsed
    time and an ETA. Messages printed while it's up scroll above both bars.

    Two Progress instances share one Live rather than living in one Progress, because a
    Progress lists tasks in creation order: per-file tasks come and go, so in a single
    Progress the overall bar could only ever sit at the top.
    """

    def __init__(self, total: int, status: PipelineStatus | None = None) -> None:
        self.total = total
        self.status = status or PipelineStatus()
        self.done = 0
        self.counts: dict[str, int] = {}
        self.files = progress_bar()
        self.overall = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("• ETA"),
            TimeRemainingColumn(),
            console=console,
        )
        self.overall_task = self.overall.add_task(self._describe(), total=total)
        self.live = Live(Group(self.files, self.overall), console=console, refresh_per_second=4)

    def __enter__(self) -> BatchDisplay:
        self.live.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self.live.__exit__(*exc)

    def _describe(self) -> str:
        left = self.total - self.done
        text = f"[bold]Overall[/] {self.done}/{self.total} · {left} left"
        tally = [label.format(n=self.counts[status])
                 for status, label in _TALLY if self.counts.get(status)]
        return text + (f"  {'  '.join(tally)}" if tally else "")

    def encode_task(self, description: str) -> tuple[_EncodeProgress, TaskID]:
        """A (progress, task_id) pair to hand to transcode(). Its percentage updates the
        file's own bar and move the overall bar along with it, so the ETA reflects work
        inside a long encode rather than only jumping once per finished file."""
        task_id = self.files.add_task(description, total=100)
        return _EncodeProgress(self, task_id), task_id

    def end_encode(self, task_id: TaskID) -> None:
        self.files.remove_task(task_id)

    def file_fraction(self, fraction: float) -> None:
        self.overall.update(self.overall_task, completed=self.done + min(max(fraction, 0.0), 1.0))

    def finish(self, result: dict) -> None:
        self.done += 1
        status = result.get("status", "")
        self.counts[status] = self.counts.get(status, 0) + 1
        self.overall.update(self.overall_task, completed=self.done, description=self._describe())


class _EncodeProgress:
    """Stands in for the Progress that transcode() reports to — it only ever calls
    `update(task_id, completed=pct)` — and forwards each percentage to the batch too."""

    def __init__(self, display: BatchDisplay, task_id: TaskID) -> None:
        self.display = display
        self.task_id = task_id

    def update(self, task_id: TaskID, completed: float) -> None:
        self.display.files.update(task_id, completed=completed)
        self.display.file_fraction(completed / 100)
        self.display.status.progress(completed)


# ── Probing ───────────────────────────────────────────────────────────

def probe(path: Path) -> tuple[int, float, int, bool] | None:
    """Return (height, duration_seconds, bit_depth, has_subtitle), or None if ffprobe can't
    read it. Subtitle presence is measured from the real streams rather than trusted from the
    filename, the same way resolution and bit depth are."""
    payload = ffprobe_common.probe(path)
    if payload is None:
        return None

    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or "height" not in video:
        return None

    try:
        duration = float(payload.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0

    depth = ffprobe_common.bit_depth(video.get("pix_fmt", ""), video.get("bits_per_raw_sample"))
    has_sub = any(s.get("codec_type") == "subtitle" for s in streams)
    return int(video["height"]), duration, depth, has_sub


# ── Filesystem helpers ────────────────────────────────────────────────

def _is_video(f: Path) -> bool:
    if f.name.startswith(".") or not f.is_file():
        return False
    if f.suffix.lower() in (".tmp", ".processing"):
        return False
    return f.suffix.lower() in VIDEO_EXTS


def scan(root: Path):
    """Video files directly in *root* — never subfolders, so trip folders and any
    staging folder you keep by hand are left alone."""
    return (f for f in sorted(root.iterdir()) if _is_video(f))


SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}


def scan_subs(root: Path):
    """Subtitle files directly in *root* — same root-only, dotfile-skipping convention as
    scan(), so a subtitle only needs to be dropped where videos already are."""
    for f in sorted(root.iterdir()):
        if f.name.startswith(".") or not f.is_file():
            continue
        if f.suffix.lower() in SUB_EXTS:
            yield f


def all_videos(root: Path) -> list[Path]:
    """Every video file anywhere under *root* — root itself plus every trip folder, _review/
    and _incoming/ excluded — so a subtitle dropped in after its video has already been sorted
    can still find it, wherever it currently lives."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        _skip_incoming(root, dirpath, dirnames)
        if REVIEW_DIR in dirnames:
            dirnames.remove(REVIEW_DIR)
        for name in filenames:
            p = Path(dirpath) / name
            if _is_video(p):
                found.append(p)
    return found


def find_folder(base: Path, name: str) -> Path | None:
    """Case-insensitive recursive search for a folder called *name*."""
    name_lower = name.lower()
    for dirpath, dirnames, _files in os.walk(base):
        if REVIEW_DIR in dirnames:
            dirnames.remove(REVIEW_DIR)
        for d in dirnames:
            if d.lower() == name_lower:
                return Path(dirpath) / d
    return None


def recover_interrupted(root: Path) -> list[str]:
    """Undo a half-finished run: restore *.processing sources, drop *.tmp outputs.

    A daemon that gets killed mid-encode must come back without losing a source file.
    """
    recovered = []
    for f in sorted(root.iterdir()):
        if f.suffix.lower() == ".processing" and f.is_file():
            original = f.parent / f.stem
            try:
                f.rename(original)
                recovered.append(original.name)
            except OSError as exc:
                warn(f"Could not restore {f.name}: {exc}")
    for tmp in sorted(root.glob("*.tmp")):
        try:
            tmp.unlink()
        except OSError:
            pass
    if recovered:
        warn(f"Recovered {len(recovered)} interrupted file(s): {', '.join(recovered)}")
    return recovered


def unique_path(path: Path) -> Path:
    """A non-colliding variant of *path*, so parking a file never overwrites one."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem} ({int(time.time())}){path.suffix}")


# ── Throttles ─────────────────────────────────────────────────────────

def load_ok(max_load: float | None) -> bool:
    """False when the machine is too busy to start another encode.

    This is the throttle that actually protects a live Jellyfin stream, because it reacts
    to real contention rather than guessing at a quiet hour.
    """
    if max_load is None:
        return True
    try:
        return os.getloadavg()[0] <= max_load
    except (OSError, AttributeError):   # not available on Windows
        return True


# ── Encoder setup ─────────────────────────────────────────────────────

class EncoderPlan:
    """Which backend to use, and the HandBrake args that select it per preset."""

    def __init__(self, backend, auto_selected: bool, quality_override, presets: dict,
                 available: set, bit_depth_pref: str):
        self.backend = backend
        self.auto_selected = auto_selected
        self.quality_override = quality_override
        self._presets = presets   # preset stem -> (encoder, quality)
        self.available = available
        self.bit_depth_pref = bit_depth_pref

    def wants_10bit(self, source_depth: int) -> bool:
        return core.want_10bit(self.bit_depth_pref, source_depth)

    def has_10bit(self, backend=None) -> bool:
        backend = backend or self.backend
        return bool(backend.encoder_10bit) and backend.encoder_10bit in self.available

    def args_for(self, preset_stem: str, force_cpu: bool = False,
                 ten_bit: bool = False) -> list[str]:
        encoder, cq = self._presets[preset_stem]
        backend = core.BACKENDS["cpu"] if force_cpu else self.backend
        return core.encoder_args(
            backend, encoder, cq, self.quality_override,
            ten_bit=ten_bit, available=self.available,
        )


def build_encoder_plan(args, preset_stems: list[str]) -> EncoderPlan:
    presets = {}
    for stem in preset_stems:
        settings = read_preset_settings(stem)
        presets[stem] = (
            settings.get("VideoEncoder", ""),
            float(settings.get("VideoQualitySlider", 22)),
        )

    # Every preset is authored against the same encoder; the first is representative.
    preset_encoder = presets[preset_stems[0]][0]

    preference = args.gpu or os.environ.get("VIDLIB_GPU", "auto")
    if preference not in core.GPU_CHOICES:
        die(f"Unknown --gpu value: {preference} (choose from {', '.join(core.GPU_CHOICES)})")

    available = available_encoders()
    if not available:
        warn("Could not read HandBrake's encoder list — assuming the preset's own encoder works")
        available = {preset_encoder}

    backend, auto_selected = core.select_backend(
        preference, available, preset_encoder,
    )
    if backend is None:
        die(
            f"No usable encoder: '{preference}' is not available in this HandBrake build. "
            f"Available: {', '.join(sorted(available)) or 'none'}"
        )

    return EncoderPlan(backend, auto_selected, args.quality, presets,
                       available, args.bit_depth)


# ── Per-file pipeline ─────────────────────────────────────────────────

MAX_FAILURES = 3


def should_process(video: Path, root: Path, db: LibraryDB) -> bool:
    """Whether a file in the root is worth another pass.

    Everything in the root is a candidate — a known file that is still here means you moved
    it back to be re-filed. Only three things are skipped, all to stop a long-running watcher
    spinning on the same file every interval.
    """
    row = db.get(rel_path(root, video))
    if row is None:
        return True
    if row.status == "awaiting_trip" and not core.parse_stem(video.stem).trip:
        return False   # nothing changed since we last warned about it
    if row.status == "failed" and row.failures >= MAX_FAILURES:
        return False   # give up rather than retry a broken file forever
    if row.status == "skipped" and row.reason == "destination exists":
        # Stays in the root until you move or rename something, and re-encoding it on
        # every poll in the meantime would burn the GPU indefinitely. -F retries it.
        return False
    return True


def already_done(row: Video | None, src_depth: int, bit_depth_pref: str) -> bool:
    """True when this exact file is already compressed and only needs sorting.

    "This exact file" is what the database knows by path — kept true across hand renames and
    moves by sync_library() — so a file you staged in a folder and moved back to the root is
    never re-encoded, whatever its name says. The one exception is a forced -B whose depth
    the file doesn't have: asking for 8-bit re-encodes a 10-bit file, and vice versa.
    """
    if row is None or row.status not in DONE_STATUSES:
        return False
    if bit_depth_pref == "8":
        return src_depth <= 8
    if bit_depth_pref == "10":
        return src_depth > 8
    return True


def plan_destination(root: Path, stem: str) -> tuple[str, Path | None]:
    """Return (trip, existing_folder_or_None) for a tagged stem."""
    trip = core.parse_stem(stem).trip
    if not trip:
        return "", None
    return trip, find_folder(root, trip)


def resolve_duplicate(root: Path, db: LibraryDB, video: Path, height: int, duration: float,
                      policy: str) -> tuple[str | None, Video | None]:
    """Compare *video* with the library's copy of the same video, wherever it's filed.

    "The same video" is the same set of people, title and collection, whatever their order,
    case or tags (core.parse_name). Copies parked in _review/ are a decision still waiting on
    you, so they're never compared against. Returns (verdict, existing) — both None when
    there's no copy.
    """
    copies = [
        c for c in db.same_video(video.stem, exclude_path=rel_path(root, video))
        if c.status != "review" and (root / c.path).is_file()
    ]
    if not copies:
        return None, None
    existing = min(copies, key=lambda c: (c.status != "sorted", c.path))

    # A 1440p and a 1080p source both come out of hw-1080 at 1080p, so where the database
    # remembers what the existing file started as, that beats its current height.
    old_height = existing.source_height or existing.height
    old_duration = existing.duration
    if not old_height or not old_duration:
        probed = probe(root / existing.path)
        if probed is None:
            return core.AMBIGUOUS, existing
        old_height = old_height or probed[0]
        old_duration = old_duration or probed[1]

    verdict = core.duplicate_verdict(height, duration, old_height, old_duration)
    if policy == "review" and verdict != core.DOWNGRADE:
        return core.AMBIGUOUS, existing
    return verdict, existing


def park_for_review(root: Path, video: Path, reason: str) -> Path:
    review = root / REVIEW_DIR
    review.mkdir(exist_ok=True)
    dest = unique_path(review / video.name)
    shutil.move(str(video), str(dest))
    warn(f"{video.name} → {REVIEW_DIR}/ ({reason})")
    return dest


def process(video: Path, root: Path, db: LibraryDB, plan: EncoderPlan | None, args,
            display: BatchDisplay) -> dict:
    """Optimise, tag and sort one file. Returns a result row for the summary table."""
    name = video.name

    probed = probe(video)
    if probed is None:
        warn(f"Skipping (ffprobe could not read it): {name}")
        return {"file": name, "status": "skipped", "reason": "unreadable"}
    src_height, src_duration, src_depth, src_has_sub = probed

    row = db.get(rel_path(root, video))
    # A file the database knows as compressed is only tagged, deduplicated and sorted. -F
    # forces an encode anyway.
    skip_encode = not args.force and already_done(row, src_depth, args.bit_depth)

    # 10-bit tracks the source by default, so HDR and 10-bit grades aren't flattened to
    # 8-bit — but a backend without a 10-bit encoder quietly stays 8-bit rather than
    # jumping to different hardware, so say when that happens.
    if plan is None:
        ten_bit = src_depth > 8          # no encoder to consult; display only
    else:
        ten_bit = plan.wants_10bit(src_depth)
        if ten_bit and not plan.has_10bit():
            if not skip_encode:   # nothing will be encoded, so nothing to warn about
                warn(
                    f"{name}: {src_depth}-bit source but {plan.backend.name} has no 10-bit "
                    f"encoder here — encoding 8-bit"
                )
            ten_bit = False
    target_depth = 10 if ten_bit else 8

    # A file that isn't encoded keeps its own depth, so its tag must describe that — the
    # same depth _finish() retags it with.
    tagged_stem = core.apply_tags(
        video.stem, src_height, src_depth if skip_encode else target_depth, src_has_sub,
    )
    trip, dest_folder = plan_destination(root, tagged_stem)

    # Pre-flight: reject a clear downgrade before spending an hour encoding it.
    verdict, existing = resolve_duplicate(
        root, db, video, src_height, src_duration, args.on_duplicate,
    )
    if verdict == core.DOWNGRADE and existing is not None and args.on_duplicate != "ignore":
        if args.dry_run:
            info(f"{name}: downgrade of {existing.path} — would park in {REVIEW_DIR}/")
        else:
            parked = park_for_review(root, video, f"downgrade of {existing.path}")
            # The parked file keeps a "done" status if it was already compressed, so moving
            # it back out of _review/ later doesn't re-encode it.
            record(db, root, parked, "review", reason="downgrade",
                   compared_with=existing.path, height=src_height, bit_depth=src_depth,
                   has_sub=src_has_sub, duration=src_duration, source_height=src_height)
            forget(db, root, video)
            db.log_event(rel_path(root, parked), "parked", f"downgrade of {existing.path}")
            return {"file": name, "status": "review", "reason": "downgrade",
                    "path": rel_path(root, parked)}
        return {"file": name, "status": "review", "reason": "downgrade"}

    preset_stem = core.select_preset(src_height, args.preset_1080, args.preset_720)

    if args.dry_run:
        if not trip:
            dest_desc = "(stays in root — no trip field)"
        elif dest_folder is not None:
            # Show the folder that was actually matched, so a case-insensitive hit on an
            # existing folder is distinguishable from creating a near-duplicate of it.
            dest_desc = f"{dest_folder.relative_to(root)}/  [dim](existing)[/]"
        else:
            dest_desc = f"{trip}/  [yellow](would be created)[/]"
        extra = f"  [{verdict} vs {existing.path}]" if verdict and existing else ""
        depth_note = (
            f", {src_depth}-bit source, encoder not checked"
            if plan is None
            else f", {src_depth}-bit → {'10' if ten_bit else '8'}-bit"
        )
        action = (
            "already optimised — sort only"
            if skip_encode
            else f"{preset_stem}  ({core.resolution_label(src_height)} source{depth_note})"
        )
        info(
            f"{name}\n"
            f"    encode   {action}\n"
            f"    rename   {tagged_stem}{'.mp4' if not skip_encode else video.suffix}\n"
            f"    move to  {dest_desc}{extra}"
        )
        return {"file": name, "status": "dry-run"}

    # Past here the run is real, and a real run requires HandBrake — main() only leaves
    # plan unset for a dry run on a machine without it.
    assert plan is not None

    if skip_encode:
        info(f"{name}  [dim]already optimised — sorting only[/]")
        size = video.stat().st_size
        return _finish(video, video, root, db, plan, args, row,
                       size, size, src_height, src_height, src_duration,
                       src_depth, src_has_sub, encoded=False)

    # ── Encode ────────────────────────────────────────────────────
    if not wait_stable(video):
        info(f"Skipping (still being written): {name}")
        return {"file": name, "status": "skipped", "reason": "still changing"}

    orig_size = video.stat().st_size
    src_processing = video.parent / f"{name}.processing"
    tmp = root / f"{tagged_stem}.mp4.tmp"

    try:
        video.rename(src_processing)
    except OSError:
        warn(f"Could not lock for processing: {name}")
        return {"file": name, "status": "failed", "original_size": orig_size}

    preset_file, preset_label = load_preset(preset_stem)

    progress, task = display.encode_task(
        f"[cyan]{name}[/]  [dim]{fmt_size(orig_size)} · {preset_stem}[/]"
    )
    cancel = display.status.cancel
    display.status.encoding(preset_stem, plan.backend.name)
    try:
        succeeded = transcode(
            src_processing, tmp, preset_file, preset_label, progress, task,
            extra_args=plan.args_for(preset_stem, ten_bit=ten_bit),
            low_priority=not args.no_nice, cancel=cancel,
        )
        # An auto-selected backend that fails gets one CPU retry; an explicitly requested
        # one does not — the same rule _common/encoders.py follows.
        if (not succeeded and not cancel.is_set() and plan.auto_selected
                and plan.backend.name != "cpu"):
            warn(f"{plan.backend.name} encode failed for {name} — retrying on CPU")
            if tmp.exists():
                tmp.unlink()
            progress.update(task, completed=0)
            display.status.encoding(preset_stem, "cpu")
            succeeded = transcode(
                src_processing, tmp, preset_file, preset_label, progress, task,
                extra_args=plan.args_for(preset_stem, force_cpu=True, ten_bit=ten_bit),
                low_priority=not args.no_nice, cancel=cancel,
            )
    finally:
        display.end_encode(task)

    if cancel.is_set():
        # Stopped from the web page: put the file back as it was, and don't count it as a
        # failure — it's encoded again once the pipeline is resumed.
        if tmp.exists():
            tmp.unlink()
        src_processing.rename(video)
        info(f"Stopped: {name}")
        return {"file": name, "status": "stopped"}

    if not succeeded or not tmp.exists():
        if tmp.exists():
            tmp.unlink()
        src_processing.rename(video)
        failures = (row.failures if row else 0) + 1
        warn(
            f"Transcode failed: {name}"
            + (f" — giving up after {failures} attempts" if failures >= MAX_FAILURES else "")
        )
        record(db, root, video, "failed", failures=failures, height=src_height,
               duration=src_duration, bit_depth=src_depth, has_sub=src_has_sub)
        db.log_event(rel_path(root, video), "failed", f"attempt {failures}")
        return {"file": name, "status": "failed", "original_size": orig_size}

    comp_size = tmp.stat().st_size
    pct = (1 - comp_size / orig_size) * 100 if orig_size else 0

    if comp_size < orig_size:
        final = unique_path(root / f"{tagged_stem}.mp4")
        tmp.rename(final)
        move_to_trash(str(src_processing))
        console.print(
            f"  [green]✓[/] {name}  "
            f"[dim]{fmt_size(orig_size)} → {fmt_size(comp_size)} ({pct:+.1f}%)[/]"
        )
    else:
        # The original was already better than we can do — keep it, just retag it.
        tmp.unlink()
        src_processing.rename(video)
        final = video
        wanted = video.with_name(f"{tagged_stem}{video.suffix}")
        if wanted != video:
            final = unique_path(wanted)
            video.rename(final)
        comp_size = orig_size
        console.print(f"  [yellow]≈[/] {name}  [dim]original kept ({pct:+.1f}%)[/]")

    # ── Re-probe, since the encode decides the real resolution ────
    reprobed = probe(final)
    out_height, out_duration = reprobed[:2] if reprobed else (src_height, src_duration)
    out_depth = reprobed[2] if reprobed else src_depth
    out_has_sub = reprobed[3] if reprobed else src_has_sub

    return _finish(video, final, root, db, plan, args, row,
                   orig_size, comp_size, src_height, out_height, out_duration,
                   out_depth, out_has_sub, encoded=True)


def _finish(video: Path, final: Path, root: Path, db: LibraryDB, plan: EncoderPlan, args,
            row: Video | None, orig_size: int, comp_size: int, src_height: int,
            out_height: int, out_duration: float, out_depth: int = 8,
            has_sub: bool = False, encoded: bool = True) -> dict:
    """Retag to the measured resolution, resolve duplicates, sort, and record the result.

    Shared by the encode path and the already-done path so a file that comes back to the
    root reaches exactly the same destination as one that was just transcoded. A file that
    wasn't encoded here keeps what its row already knew about its origins (source height,
    original size, the backend that encoded it).
    """
    name = video.name

    retagged = core.apply_tags(final.stem, out_height, out_depth, has_sub)
    if retagged != final.stem:
        renamed = unique_path(final.with_name(f"{retagged}{final.suffix}"))
        final.rename(renamed)
        final = renamed

    fields: dict[str, object] = {
        "height": out_height,
        "bit_depth": out_depth,
        "has_sub": has_sub,
        "duration": out_duration,
        "source_height": src_height,
        "original_size": orig_size,
        "backend": plan.backend.name if encoded else "",
    }
    if not encoded and row is not None:
        fields.update(source_height=row.source_height or src_height,
                      original_size=row.original_size or orig_size, backend=row.backend)

    # ── Duplicate resolution, re-decided on the finished file ─────
    trip, dest_folder = plan_destination(root, final.stem)
    verdict, existing = resolve_duplicate(
        root, db, final, out_height, out_duration, args.on_duplicate,
    )

    if verdict and existing and not core.wins(verdict) and args.on_duplicate != "ignore":
        parked = park_for_review(root, final, f"{verdict} vs {existing.path}")
        record(db, root, parked, "review", reason=verdict, compared_with=existing.path,
               **fields)  # pyright: ignore[reportArgumentType]
        forget(db, root, video, final)
        db.log_event(rel_path(root, parked), "parked", f"{verdict} vs {existing.path}")
        return {"file": name, "status": "review", "reason": verdict,
                "path": rel_path(root, parked),
                "original_size": orig_size, "compressed_size": comp_size}

    # ── Sort ──────────────────────────────────────────────────────
    if not trip:
        if row is None or row.status != "awaiting_trip":
            warn(f"No trip field, leaving in root: {final.name}")
        record(db, root, final, "awaiting_trip", **fields)  # pyright: ignore[reportArgumentType]
        forget(db, root, video)
        return {"file": name, "status": "awaiting_trip", "path": rel_path(root, final),
                "original_size": orig_size, "compressed_size": comp_size}

    if dest_folder is None:
        dest_folder = root / trip
        dest_folder.mkdir(parents=True, exist_ok=True)

    replaced = ""
    if verdict and existing and core.wins(verdict):
        move_to_trash(str(root / existing.path))
        db.remove(existing.path)
        db.log_event(existing.path, "replaced", f"by {final.name} ({verdict})")
        ok(f"Replaced {existing.path} ({verdict})")
        replaced = existing.path

    dest = dest_folder / final.name
    if dest.exists():
        warn(f"Already exists, leaving in root: {dest}")
        record(db, root, final, "skipped", reason="destination exists", replaced=replaced,
               **fields)  # pyright: ignore[reportArgumentType]
        forget(db, root, video)
        return {"file": name, "status": "skipped", "reason": "destination exists",
                "path": rel_path(root, final),
                "original_size": orig_size, "compressed_size": comp_size}

    shutil.move(str(final), str(dest))
    ok(f"{final.name} → {trip}/")

    record(db, root, dest, "sorted", replaced=replaced,
           **fields)  # pyright: ignore[reportArgumentType]
    forget(db, root, video, final)
    db.log_event(rel_path(root, dest), "encoded" if encoded else "sorted",
                 f"from {name}" if name != dest.name else "")

    return {"file": name, "status": "sorted", "trip": trip, "path": rel_path(root, dest),
            "original_size": orig_size, "compressed_size": comp_size}


# ── Merging parts ────────────────────────────────────────────────────

def run_merge(root: Path, db: LibraryDB, job: MergeJob, status: PipelineStatus) -> None:
    """Join *job*'s parts with concatvid, which re-encodes only what doesn't match, checks
    the result's length against the parts and only then trashes them.

    The joined file is written to _incoming/merge-<id>/ (never scanned) and moved into the
    root only once its parts are gone — i.e. concatvid's check passed. There the pipeline
    files it: sorted only when every part was encoded by vidlib (its row is recorded as
    compressed), encoded like any new arrival otherwise. A join that failed the check stays
    put, `unverified`, with the parts untouched, for the page to discard."""
    parts = [root / p for p in job.paths]
    if any(not p.is_file() for p in parts):
        status.update_merge(job, "failed", "a part was moved or removed while it waited")
        return
    rows = [db.get(p) for p in job.paths]
    all_encoded = all(r is not None and r.backend for r in rows)
    backend = next((r.backend for r in rows if r is not None and r.backend), "")

    staging = root / INCOMING_DIR / f"merge-{job.id}"
    staging.parent.mkdir(exist_ok=True)       # never parents=True: the root must exist
    staging.mkdir(exist_ok=True)
    out = staging / f"{job.stem}{job.ext}"
    status.update_merge(job, "running", f"joining {len(parts)} parts")
    info(f"Merging {len(parts)} parts → {out.name}")
    cmd = [sys.executable, "-m", "toolboxcli.concatvid.cli", *map(str, parts),
           "-o", str(out), "-y"]
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env,
                                **detached_kwargs())
    except OSError as exc:
        status.update_merge(job, "failed", f"couldn't run concatvid: {exc}")
        return

    def watch() -> None:                       # Stop now on the page ends the merge
        while proc.poll() is None:
            if status.cancel.wait(0.5) and proc.poll() is None:
                proc.terminate()
                return
    threading.Thread(target=watch, daemon=True).start()
    last = ""
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.strip()
            if text:
                last = text
                status.update_merge(job, detail=text[:200])
        proc.wait()
    except BaseException:
        proc.terminate()
        proc.wait()
        raise

    parts_gone = all(not p.exists() for p in parts)
    if out.is_file() and parts_gone:
        final = unique_path(root / out.name)
        out.rename(final)
        try:
            staging.rmdir()
        except OSError:
            pass
        for path in job.paths:
            db.remove(path)
            db.log_event(path, "merged", f"into {final.name}")
        if all_encoded:
            probed = probe(final)
            height, duration, depth, has_sub = probed or (0, 0.0, 8, False)
            record(db, root, final, "compressed", height=height, duration=duration,
                   bit_depth=depth, has_sub=has_sub, source_height=height, backend=backend)
        db.log_event(rel_path(root, final), "merged", f"from {len(parts)} parts")
        ok(f"Merged → {final.name}")
        status.update_merge(job, "done", f"→ {final.name}", rel_path(root, final))
        return
    if status.cancel.is_set():
        if out.is_file() and not parts_gone:
            out.unlink()
        try:
            staging.rmdir()
        except OSError:
            pass
        status.update_merge(job, "stopped", "stopped; the parts are untouched")
        return
    if out.is_file():
        warn(f"Merged {out.name}, but couldn't confirm it holds every part — parts kept")
        status.update_merge(job, "unverified",
                            "the joined file couldn't be confirmed to hold every part, so "
                            "the parts were kept", rel_path(root, out))
        return
    try:
        staging.rmdir()
    except OSError:
        pass
    warn(f"Merge failed: {last or f'concatvid exited {proc.returncode}'}")
    status.update_merge(job, "failed", last or f"concatvid exited {proc.returncode}")


# ── Subtitle merging ─────────────────────────────────────────────────

def merge_subtitles(root: Path, db: LibraryDB, args) -> list[dict]:
    """Match waiting subtitle files to videos anywhere in the library and merge unambiguous
    1:1 pairs with addsub.

    Runs every poll after the encode/sort pass, so a subtitle dropped next to a brand-new
    video is picked up the moment that video is filed, and one dropped in for a video that's
    already sorted into a trip folder is picked up wherever it now lives.
    """
    subs = list(scan_subs(root))
    if not subs:
        return []

    videos = all_videos(root)
    matches, skipped = core.match_subtitles([v.stem for v in videos], [s.name for s in subs])

    for sub_name, reason in skipped.items():
        warn(f"{sub_name}: ambiguous subtitle match ({reason}) — leaving both alone")

    if not matches:
        return []

    video_by_stem = {v.stem: v for v in videos}
    sub_by_name = {s.name: s for s in subs}
    return [
        _merge_one(root, video_by_stem[video_stem], sub_by_name[sub_name], db, args)
        for sub_name, video_stem in matches.items()
    ]


def _merge_one(root: Path, video: Path, sub: Path, db: LibraryDB, args) -> dict:
    if args.dry_run:
        probed = probe(video)
        if probed is not None:
            height, _duration, depth, _has_sub = probed
            preview = core.apply_tags(video.stem, height, depth, True) + video.suffix
        else:
            preview = "(unreadable — merge would still be attempted)"
        info(f"{sub.name} → {video.name}\n    merge    would run addsub, then rename to {preview}")
        return {"file": sub.name, "status": "dry-run"}

    result = subprocess.run(["addsub", str(video), str(sub)])
    if result.returncode != 0:
        warn(f"addsub failed merging {sub.name} into {video.name}")
        return {"file": sub.name, "status": "failed"}

    probed = probe(video)
    if probed is None:
        warn(f"{video.name}: merged subtitle but ffprobe can't read the result")
        return {"file": sub.name, "status": "failed"}
    height, _duration, depth, has_sub = probed

    retagged = core.apply_tags(video.stem, height, depth, has_sub)
    final = video
    if retagged != video.stem:
        renamed = unique_path(video.with_name(f"{retagged}{video.suffix}"))
        video.rename(renamed)
        final = renamed

    # The merge rewrote the file, so its size and mtime changed too: update them along with
    # the path, or a later hand rename couldn't be matched back to this row. A video the
    # database has never seen (new input still in the root) gets no row invented for it.
    old_rel, new_rel = rel_path(root, video), rel_path(root, final)
    if db.get(old_rel) is not None:
        db.move(old_rel, new_rel)
        row = db.get(new_rel)
        assert row is not None
        st = final.stat()
        row.has_sub, row.size, row.mtime, row.updated_at = has_sub, st.st_size, st.st_mtime, ""
        db.upsert(row)
        db.log_event(new_rel, "subtitle merged", sub.name)

    ok(f"{sub.name} → merged into {final.name}")
    return {"file": final.name, "status": "sub-merged"}


def merge_only(root: Path, db: LibraryDB, args) -> None:
    """One subtitle-merging pass, then exit — the tail of a normal poll on its own.

    Needs only addsub and ffprobe, so it's dispatched ahead of the HandBrake check and the
    encoder probe: merging a subtitle into an already-sorted video shouldn't require a
    machine that can encode, nor wait on a probe it will never use.
    """
    require_tool("ffprobe", "part of ffmpeg")
    require_tool("addsub", "ships with this toolbox")

    if not list(scan_subs(root)):
        info(f"No subtitle files waiting in {root}")
        return

    results = merge_subtitles(root, db, args)
    if results:
        show_summary(results)


# ── Reporting ─────────────────────────────────────────────────────────

def show_report(root: Path, db: LibraryDB) -> None:
    below = [v for v in db.all() if 0 < v.height < 1080]
    if below:
        tbl = Table(
            title="Below 1080p — candidates for upscaling",
            box=box.ROUNDED, title_style="bold cyan",
        )
        tbl.add_column("File", style="cyan", no_wrap=True)
        tbl.add_column("Resolution", justify="center")
        tbl.add_column("Depth", justify="center")
        tbl.add_column("Location")
        for video in below:
            folder = str(PurePosixPath(video.path).parent)
            tbl.add_row(
                escape(video.filename), video.resolution, f"{video.bit_depth}-bit",
                "(root)" if folder == "." else escape(folder),
            )
        console.print(tbl)
    else:
        info("Nothing below 1080p on record.")

    unnamed = [v for v in db.search(unnamed=True) if v.status != "review"]
    if unnamed:
        warn(f"{len(unnamed)} file(s) aren't named People - Title - Collection, so they can't be "
             "matched as duplicates or searched by person/collection — `vidlib -U` lists them")

    review = root / REVIEW_DIR
    parked = sorted(review.iterdir()) if review.is_dir() else []
    parked = [p for p in parked if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    if parked:
        tbl = Table(
            title=f"Waiting in {REVIEW_DIR}/", box=box.ROUNDED, title_style="bold yellow",
        )
        tbl.add_column("File", style="yellow", no_wrap=True)
        tbl.add_column("Reason")
        tbl.add_column("Compared with")
        for p in parked:
            row = db.get(rel_path(root, p))
            tbl.add_row(escape(p.name), (row.reason if row else "") or "—",
                        escape(row.compared_with if row else "") or "—")
        console.print()
        console.print(tbl)


def _filters_desc(args) -> str:
    parts = [f"{label} '{value}'" for label, value in (
        ("person", args.person), ("collection", args.collection),
        ("resolution", args.resolution), ("tag", args.tag),
    ) if value]
    if args.unnamed:
        parts.insert(0, "not named People - Title - Collection")
    return ", ".join(parts)


def _location(video: Video) -> str:
    folder = str(PurePosixPath(video.path).parent)
    return "(root)" if folder == "." else folder


def list_library(root: Path, db: LibraryDB, args) -> None:
    """Print the library's videos — all of them, or those matching -a/-c/-R/-T — as a table,
    or write their names to the -l FILE.

    The whole library, trip folders included; `_review/` (a decision still waiting on you)
    and files that have gone are left out. Filters combine, and are case-insensitive; text
    filters match part of a value, so `-a ali` finds Alice.
    """
    videos = [
        v for v in db.search(person=args.person, collection=args.collection,
                             resolution=args.resolution, tag=args.tag, unnamed=args.unnamed)
        if v.status != "review"
    ]
    videos.sort(key=lambda v: v.filename.lower())
    desc = _filters_desc(args)
    count = f"{len(videos)} file{'' if len(videos) == 1 else 's'}"

    if not args.list_files:
        if videos:
            tbl = Table(box=box.SIMPLE_HEAD, show_edge=False)
            tbl.add_column("File", style="cyan", overflow="fold")
            tbl.add_column("Resolution", justify="center")
            tbl.add_column("Depth", justify="center")
            tbl.add_column("Location", overflow="fold")
            for v in videos:
                tbl.add_row(escape(v.filename), v.resolution or "?", f"{v.bit_depth}-bit",
                            escape(_location(v)))
            console.print(tbl)
        info(f"{count} {'matching ' + desc if desc else 'in ' + str(root)}")
        return

    out = Path(args.list_files).expanduser()
    if out.exists() and confirm(f"{out} exists — overwrite?", default="n") != "y":
        info("Cancelled")
        return
    names = [v.filename for v in videos]
    try:
        out.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")
    except OSError as e:
        die(f"Couldn't write {out}: {e}")
    ok(f"{count} → {out}")


def show_duplicates(db: LibraryDB) -> None:
    """Every group of files the library holds more than once — the same people, title and
    collection, whatever the order, case or tags. A report only: nothing is moved."""
    groups = db.duplicate_groups()
    if not groups:
        ok("No duplicates in the library.")
        return
    for group in groups:
        name = group[0].name
        people = ", ".join(name.people) or "—"
        tbl = Table(
            title=escape(f"{people} · {name.title} · {name.collection or '(no collection)'}"),
            title_justify="left", title_style="bold yellow", box=box.SIMPLE_HEAD,
            show_edge=False,
        )
        tbl.add_column("File", style="cyan", overflow="fold")
        tbl.add_column("Resolution", justify="center")
        tbl.add_column("Duration", justify="right")
        tbl.add_column("Size", justify="right")
        tbl.add_column("Location", overflow="fold")
        for v in group:
            minutes, seconds = divmod(int(v.duration), 60)
            tbl.add_row(escape(v.filename), v.resolution or "?",
                        f"{minutes}:{seconds:02d}" if v.duration else "?",
                        fmt_size(v.size) if v.size else "?", escape(_location(v)))
        console.print(tbl)
        console.print()
    files = sum(len(g) for g in groups)
    warn(f"{len(groups)} video(s) held more than once ({files} files) — nothing was changed")


def show_summary(results: list[dict]) -> None:
    if not results:
        return
    counts: dict[str, int] = {}
    total_orig = total_comp = 0
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        total_orig += r.get("original_size", 0)
        total_comp += r.get("compressed_size", 0)

    parts = [f"{n} {status}" for status, n in sorted(counts.items())]
    line = ", ".join(parts)
    if total_orig:
        net = (1 - total_comp / total_orig) * 100
        line += f"  ({fmt_size(total_orig)} → {fmt_size(total_comp)}, {net:+.1f}%)"
    console.print()
    info(line)


# ── Entry point ───────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="vidlib",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("root", nargs="?", default=os.environ.get("VIDLIB_LIBRARY") or ".",
                    help="Library root, where .vidlib.db lives (default: env VIDLIB_LIBRARY, "
                         "else .)")
    ap.add_argument("-t", "--interval", type=int, default=30,
                    help="Seconds between folder scans (default: 30)")
    ap.add_argument("-O", "--once", action="store_true",
                    help="Process existing files and exit (no continuous watch)")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="Show the planned rename, destination and duplicate verdict; change nothing")
    ap.add_argument("-r", "--report", action="store_true",
                    help="List files below 1080p and the review queue, then exit")
    ap.add_argument("-l", "--list", nargs="?", const="", default=None, metavar="FILE",
                    dest="list_files",
                    help="List the library's videos and exit (narrowed by -a/-c/-R/-T); with "
                         "a FILE, write their names there instead of printing a table")
    ap.add_argument("-a", "--person", metavar="NAME",
                    help="With -l: only videos with this person (case-insensitive, partial)")
    ap.add_argument("-c", "--collection", metavar="NAME",
                    help="With -l: only videos in this collection (case-insensitive, partial)")
    ap.add_argument("-R", "--resolution", metavar="RES",
                    help="With -l: only videos at this resolution, e.g. 720p or 720")
    ap.add_argument("-T", "--tag", metavar="TAG",
                    help="With -l: only videos carrying this [tag] (case-insensitive, partial)")
    ap.add_argument("-U", "--unnamed", action="store_true",
                    help="With -l: only videos whose names don't follow "
                         "People - Title - Collection (so can't be matched as duplicates)")
    ap.add_argument("-D", "--duplicates", action="store_true",
                    help="List videos the library holds more than once, then exit "
                         "(report only; nothing is moved)")
    ap.add_argument("-F", "--force", action="store_true",
                    help="Re-process files the library database already knows as done")
    ap.add_argument("-g", "--gpu", default=None, choices=core.GPU_CHOICES,
                    help="Encoder backend (default: auto; env VIDLIB_GPU)")
    ap.add_argument("-Q", "--quality", type=float, default=None,
                    help="Override the preset's quality value")
    ap.add_argument("-B", "--bit-depth", default="auto", choices=core.BIT_DEPTH_CHOICES,
                    help="auto matches the source (10-bit stays 10-bit, 8-bit is not "
                         "inflated); 8 or 10 forces it. Default: auto")
    ap.add_argument("-H", "--hours", default=None,
                    help="Only start encodes inside this window, e.g. 01:00-07:00")
    ap.add_argument("-L", "--max-load", type=float, default=None,
                    help="Skip a poll while the 1-minute load average exceeds this (POSIX)")
    ap.add_argument("-d", "--on-duplicate", default="auto",
                    choices=("auto", "review", "ignore"),
                    help="When the library already has this video: auto (replace on a clear win, park anything ambiguous in _review/), review (park every duplicate), ignore (file both copies). Default: auto")
    ap.add_argument("-N", "--no-nice", action="store_true",
                    help="Don't de-prioritise the encoder process")
    ap.add_argument("-p", "--preset-1080", default="hw-1080",
                    help="Preset for sources above 720p (default: hw-1080)")
    ap.add_argument("-q", "--preset-720", default="hw-720",
                    help="Preset for sources at 720p or below (default: hw-720)")
    ap.add_argument("-S", "--no-subs", action="store_true",
                    help="Don't scan for or merge matching subtitle files")
    ap.add_argument("-s", "--subs-only", action="store_true",
                    help="Merge waiting subtitle files and exit; encode and sort nothing")
    ap.add_argument("-W", "--no-web", action="store_true",
                    help="Run the pipeline without the web page (and so without downloads)")
    ap.add_argument("-X", "--download-only", nargs="?", const=".", default=None,
                    metavar="DIR",
                    help="Serve only downloads, into DIR (default: .); encode nothing")
    ap.add_argument("-P", "--port", type=int, default=DEFAULT_PORT,
                    help=f"Port for the web page on 127.0.0.1 (default: {DEFAULT_PORT})")
    ap.add_argument("-b", "--no-browser", action="store_true",
                    help="Don't open the web page in a browser on start")
    ap.add_argument("-j", "--jobs", type=int, default=2, metavar="N",
                    help="Downloads to run at once (default: 2)")
    return ap


def main() -> None:
    args = build_parser().parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        die(f"Not a directory: {root}")

    # Any filter means "list": `vidlib -a alice` is `vidlib -l -a alice`.
    if args.list_files is None and (args.person or args.collection or args.resolution
                                    or args.tag or args.unnamed):
        args.list_files = ""

    if args.subs_only and args.no_subs:
        die("--subs-only and --no-subs contradict each other")
    if args.download_only is not None and not wants_web(args):
        die("-X/--download-only serves the web page, so it can't be combined with -W, -n, "
            "-O, -r, -l (or its filters), -D or -s")

    # A dry run works on an in-memory copy of the database, so importing the old log or
    # syncing the library changes nothing on disk.
    try:
        db = LibraryDB.open(root, in_memory=args.dry_run)
    except (RuntimeError, OSError) as exc:
        die(f"Can't open the library database: {exc}")
    if legacy_db_conflict(root):
        warn(f"Both {LEGACY_DB_NAME} and {db.path.name} are in {root} — using {db.path.name}; "
             f"the old {LEGACY_DB_NAME} is left untouched")
    with db:
        try:
            migrated = import_json_log(root, db, rename=not args.dry_run)
        except RuntimeError as exc:
            die(f"Can't migrate the old log: {exc}")
        if migrated:
            info(f"Imported {migrated} entries from the old log into {db.path.name}")
        if args.download_only is not None:
            run_download_only(root, args)
            return
        if wants_web(args):
            with web_ui(root, args) as web:
                run(root, db, args, web.status, web.manager.video_settled)
        else:
            keep_awake = KeepAwake()
            status = PipelineStatus()
            status.on_change = lambda: keep_awake.set(status.busy)
            try:
                run(root, db, args, status)
            finally:
                keep_awake.close()


def wants_web(args) -> bool:
    """The web page runs alongside the continuous watch only; reports, queries, one-shot
    runs, dry runs and subtitle-only merges are terminal commands that exit."""
    return not (args.no_web or args.dry_run or args.once or args.report or args.duplicates
                or args.subs_only or args.list_files is not None)


@dataclass
class WebUI:
    server: VidlibServer
    manager: DownloadManager
    status: PipelineStatus | None


@contextmanager
def web_ui(root: Path, args, dest: Path | None = None) -> Iterator[WebUI]:
    """The download manager, the pipeline's status and the web server, for as long as the
    block runs. The manager has its own database connection, shared by the server's threads
    under its lock. In download-only mode (*dest* set) there's no pipeline to report on.

    Every change either side reports bumps the manager's version (which wakes the page's
    event stream) and re-checks whether anything is downloading or encoding, holding the
    Mac awake (KeepAwake) while it is."""
    if args.jobs < 1:
        die("--jobs must be at least 1")
    for tool in ("yt-dlp", "aria2c"):
        if shutil.which(tool) is None:
            if dest is not None:
                require_tool(tool)
            warn(f"{tool} not found — downloads from the web page will fail until it's installed")
    try:
        db = LibraryDB.open(root, shared=True)
    except (RuntimeError, OSError) as exc:
        die(f"Can't open the library database: {exc}")
    manager = DownloadManager(root, db, dest=dest, workers=args.jobs)
    status = None if dest is not None else PipelineStatus(settings=_pipeline_settings(args))
    keep_awake = KeepAwake()

    def check_awake() -> None:
        keep_awake.set(manager.busy or (status is not None and status.busy))

    manager.after_change = check_awake
    if status is not None:
        status.on_change = manager.notify
    try:
        server = VidlibServer(manager, args.port, pipeline=status)
    except OSError as exc:
        db.close()
        if exc.errno == errno.EADDRINUSE:
            die(f"Port {args.port} is in use — is vidlib already running? Open "
                f"http://127.0.0.1:{args.port}/, or pick another port with -P")
        die(f"Can't start the web page: {exc}")
    try:
        imported = manager.start()
        if imported:
            info(f"Imported {imported} entries from the old video-download queue")
        server.start()
        ok(f"Web page at {server.url}")
        if not args.no_browser:
            webbrowser.open(server.url)
        yield WebUI(server, manager, status)
    finally:
        server.stop()
        manager.shutdown()
        keep_awake.close()
        db.close()


def _pipeline_settings(args) -> dict[str, object]:
    """The flags the page shows (it can't change them)."""
    return {"interval": args.interval, "hours": args.hours or "", "max_load": args.max_load,
            "gpu": args.gpu or os.environ.get("VIDLIB_GPU") or "auto",
            "on_duplicate": args.on_duplicate, "bit_depth": args.bit_depth}


def run_download_only(root: Path, args) -> None:
    dest = Path(args.download_only).expanduser().resolve()
    if not dest.is_dir():
        die(f"Not a directory: {dest}")
    if dest == root:
        die("-X needs a folder other than the library root; without -X, downloads already "
            "go into the library")
    banner = Table.grid(padding=(0, 2))
    banner.add_column(style="bold")
    banner.add_column()
    banner.add_row("Library", str(root))
    banner.add_row("Downloads", f"{dest}  [dim](download only — nothing is encoded)[/]")
    console.print(Panel(banner, title="[bold cyan]vidlib[/]", border_style="cyan",
                        expand=False))
    with web_ui(root, args, dest=dest):
        console.print("[dim]Ctrl+C to stop; unfinished downloads resume next time.[/]")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Stopped.[/]")


def run(root: Path, db: LibraryDB, args, status: PipelineStatus | None = None,
        on_settled: Callable[[str, str], None] | None = None) -> None:
    """Reports and queries, or the pipeline. *status* is what the web page shows of the
    pipeline (and how it pauses it); *on_settled(name, path)* is told where each file that
    was in the root ended up, so a download can follow its video."""
    status = status or PipelineStatus()
    # Reports, queries and subtitle merges read the database, so bring it in line with the
    # disk first — otherwise a file moved by hand since the last poll shows where it used to be.
    if args.list_files is not None:
        sync_library(root, db)
        list_library(root, db, args)
        return

    if args.duplicates:
        sync_library(root, db)
        show_duplicates(db)
        return

    if args.report:
        sync_library(root, db)
        show_report(root, db)
        return

    if args.subs_only:
        sync_library(root, db)
        merge_only(root, db, args)
        return

    require_tool("ffprobe", "part of ffmpeg")
    # A dry run never encodes, so it doesn't need HandBrake — that's what makes it usable
    # for checking naming and sorting on a machine that isn't the server. When HandBrake
    # *is* present the plan is still built, so the dry run reports the encoder and bit
    # depth it would really use rather than guessing.
    handbrake_present = shutil.which("HandBrakeCLI") is not None
    if not args.dry_run and not handbrake_present:
        require_tool("HandBrakeCLI", "https://handbrake.fr/downloads2.php")

    # addsub is an optional dependency: missing it just disables subtitle merging rather than
    # killing what may otherwise be a long-running continuous watcher.
    addsub_present = shutil.which("addsub") is not None

    window = None
    if args.hours:
        try:
            window = core.parse_hours(args.hours)
        except ValueError as exc:
            die(f"Bad --hours value: {exc}")

    plan = build_encoder_plan(
        args, [args.preset_1080, args.preset_720],
    ) if handbrake_present else None

    banner = Table.grid(padding=(0, 2))
    banner.add_column(style="bold")
    banner.add_column()
    banner.add_row("Library", str(root))
    if plan is not None:
        banner.add_row("Encoder", f"{plan.backend.name}  [dim]({plan.backend.encoder})[/]")
        if plan.has_10bit():
            depth = ("matches source" if args.bit_depth == "auto"
                     else f"forced {args.bit_depth}-bit")
            banner.add_row("Bit depth", f"{depth}  [dim]({plan.backend.encoder_10bit})[/]")
        else:
            banner.add_row("Bit depth", "[yellow]8-bit only — no 10-bit encoder here[/]")
    if args.quality is not None:
        banner.add_row("Quality", f"{args.quality}  [dim](override)[/]")
    banner.add_row("Presets", f"{args.preset_1080} / {args.preset_720}")
    if args.no_subs:
        banner.add_row("Sub merge", "disabled (--no-subs)")
    elif addsub_present:
        banner.add_row("Sub merge", "enabled")
    else:
        banner.add_row("Sub merge", "[yellow]addsub not found — disabled[/]")
    banner.add_row("Mode", "one-shot" if args.once else f"poll every {args.interval}s")
    if window:
        banner.add_row("Hours", args.hours)
    if args.max_load is not None:
        banner.add_row("Max load", str(args.max_load))
    if args.dry_run:
        banner.add_row("Dry run", "[yellow]nothing will be changed[/]")

    console.print(Panel(banner, title="[bold cyan]vidlib[/]",
                        border_style="cyan", expand=False))
    console.print()

    if not args.dry_run:
        recover_interrupted(root)

    try:
        first_scan = True
        disk_warned = False
        while True:
            # An unplugged library disk: touch nothing (a sync would mark every video
            # missing) until it's back.
            if not root.is_dir():
                status.set_state("disk_missing", str(root))
                if not disk_warned:
                    warn(f"{root} isn't there — is the library's disk connected? Waiting.")
                    disk_warned = True
                if args.once:
                    break
                status.sleep(args.interval)
                continue
            if disk_warned:
                info(f"{root} is back")
                disk_warned = False

            if status.paused:
                status.set_state("paused")
                status.sleep(args.interval)
                continue

            if window is not None and not core.within_hours(window):
                delay = core.seconds_until(window)
                status.set_state("outside_hours", args.hours)
                info(f"Outside the encode window — sleeping {delay // 60} min")
                status.sleep(min(delay, 3600))
                continue

            if not load_ok(args.max_load):
                status.set_state("high_load", str(args.max_load))
                info(f"Load above {args.max_load} — waiting {args.interval}s")
                status.sleep(args.interval)
                continue

            # Merges queued from the page run first, one at a time, on this thread.
            while not status.paused and (job := status.next_merge()) is not None:
                status.set_state("merging", job.stem)
                run_merge(root, db, job, status)
                if on_settled is not None and job.state == "done":
                    for part in job.paths:      # a download that was a part follows too
                        on_settled(part, job.output)

            # Hand renames and moves, and files added to folders by hand, are picked up
            # before anything in the root is judged new.
            status.set_state("scanning")
            sync_library(root, db)

            # Anything sitting in the root is fair game: it's either new, or one you moved
            # back from a folder of your own to be sorted. already_done() is what stops the
            # latter being re-encoded, so this filter only has to skip the cases that would
            # otherwise spin — a name still missing its trip field, a file that keeps failing
            # to transcode, and one whose destination is taken.
            videos = [
                v for v in scan(root)
                if args.force or should_process(v, root, db)
            ]

            if videos:
                console.print(f"[bold]Found {len(videos)} file(s)[/]\n")
                results = []
                status.begin_batch([v.name for v in videos])
                try:
                    with BatchDisplay(len(videos), status) as display:
                        for v in videos:
                            if status.paused:
                                break   # the rest stay in the root until resumed
                            status.begin_file(v.name)
                            result = process(v, root, db, plan, args, display)
                            display.finish(result)
                            status.end_file(result)
                            results.append(result)
                            if on_settled is not None and result.get("path"):
                                on_settled(v.name, result["path"])
                finally:
                    status.end_batch()
                show_summary(results)
            elif args.once and first_scan:
                info("Nothing new to process.")

            # Runs every poll, including ones with no new video — that's exactly the case
            # where a subtitle was dropped in for a video that's already been sorted away.
            if addsub_present and not args.no_subs:
                sub_results = merge_subtitles(root, db, args)
                if sub_results:
                    show_summary(sub_results)

            if args.once or args.dry_run:
                break

            if videos:
                console.print(f"\n[dim]Watching (every {args.interval}s)… Ctrl+C to stop[/]")
            status.set_state("paused" if status.paused else "idle")
            status.sleep(args.interval)
            first_scan = False

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Stopped.[/]")


if __name__ == "__main__":
    main()
