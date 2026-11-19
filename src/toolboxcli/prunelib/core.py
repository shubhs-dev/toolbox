"""prunelib's pure logic: ranking, review history, selection parsing and sidecar
matching. No filesystem or terminal I/O lives here — cli.py does the scanning,
prompting and deleting and hands plain values in."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, cast

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".vob", ".3gp", ".mts", ".m2ts",
}

# sort type -> (description, which timestamp the table shows)
SORTS = {
    "size": "biggest first",
    "oldest": "oldest modified date first",
    "stale": "least recently opened first",
}

DEFAULT_COUNT = 10
DEFAULT_SORT = "size"

HISTORY_NAME = ".prunelib-history.json"
HISTORY_VERSION = 1

# Coarse filesystems (FAT, some SMB servers) store mtimes at 2 s resolution, so a
# re-stat of an untouched file can drift by up to that much.
MTIME_TOLERANCE = 2.0

# What's allowed to remain in a folder that still gets removed once its last video
# is deleted: the metadata Jellyfin (or a sidecar-writing tool) drops beside media.
LEFTOVER_FILE_EXTS = {
    ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".tbn", ".bif",
    ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup",
}
LEFTOVER_DIR_NAMES = {"extrafanart", "metadata", ".actors"}
LEFTOVER_DIR_SUFFIXES = (".trickplay",)

# Never scanned: OS/NAS recycle bins and snapshot or thumbnail stores. A Synology's
# `#recycle` in particular is where trashed files from an SMB share land, so scanning
# it would offer back the very files you just deleted. Dot-folders are skipped too.
SKIP_DIR_NAMES = {
    "#recycle", "#snapshot", "@eadir", "@recycle", "@recently-snapshot",
    "$recycle.bin", "system volume information", "lost+found",
}


def skip_dir(name: str) -> bool:
    return name.startswith(".") or name.lower() in SKIP_DIR_NAMES


@dataclass
class Candidate:
    rel: str  # path relative to the scan root, '/'-separated on every platform
    size: int
    mtime: float
    atime: float


def parse_positionals(tokens: list[str]) -> tuple[int, str]:
    """Read ``[count] [type]`` in either order. Raises ValueError with a user-facing
    message on anything else."""
    count: int | None = None
    sort: str | None = None
    for tok in tokens:
        low = tok.lower()
        if low.isdigit():
            if count is not None:
                raise ValueError(f"count given twice ({count} and {tok})")
            count = int(low)
            if count < 1:
                raise ValueError("count must be at least 1")
        elif low in SORTS:
            if sort is not None:
                raise ValueError(f"type given twice ({sort} and {low})")
            sort = low
        else:
            raise ValueError(
                f"don't understand {tok!r}: expected a count or one of {', '.join(SORTS)}"
            )
    return (count or DEFAULT_COUNT, sort or DEFAULT_SORT)


def rank(cands: list[Candidate], sort: str) -> list[Candidate]:
    """Order candidates for the given sort type; ties break on path so runs are stable."""
    if sort == "size":
        return sorted(cands, key=lambda c: (-c.size, c.rel))
    if sort == "oldest":
        return sorted(cands, key=lambda c: (c.mtime, c.rel))
    if sort == "stale":
        return sorted(cands, key=lambda c: (c.atime, c.rel))
    raise ValueError(f"unknown sort type {sort!r}")


def atime_looks_unrecorded(cands: list[Candidate]) -> bool:
    """True when no file has been read since it was last modified — the signature of a
    filesystem that isn't updating access times (Windows disables it on large NTFS
    volumes; ``noatime`` mounts do the same). 'stale' then just orders by copy date."""
    if len(cands) < 5:
        return False
    return not any(c.atime > c.mtime + 86400 for c in cands)


# ── History ──────────────────────────────────────────────────────────

def parse_history(text: str) -> dict[str, dict[str, Any]]:
    """Return the ``reviewed`` map from a history file's text; empty on anything
    unreadable, so a corrupt file costs repeat suggestions rather than a crash."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    # JSON object keys are always strings, so these casts only name what json gave us.
    reviewed: object = cast("dict[str, Any]", data).get("reviewed")
    if not isinstance(reviewed, dict):
        return {}
    entries = cast("dict[str, object]", reviewed)
    return {k: cast("dict[str, Any]", v) for k, v in entries.items() if isinstance(v, dict)}


def dump_history(reviewed: dict[str, dict[str, Any]]) -> str:
    return json.dumps(
        {"version": HISTORY_VERSION, "reviewed": dict(sorted(reviewed.items()))},
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def is_reviewed(reviewed: dict[str, dict[str, Any]], c: Candidate) -> bool:
    """A file counts as reviewed only while it's unchanged since — a file re-encoded in
    place (optimiselib) or replaced by a new release comes back for review."""
    entry = reviewed.get(c.rel)
    if entry is None:
        return False
    return entry.get("size") == c.size and abs(entry.get("mtime", 0) - c.mtime) <= MTIME_TOLERANCE


def mark_reviewed(reviewed: dict[str, dict[str, Any]], cands: list[Candidate], today: str) -> dict[str, dict[str, Any]]:
    out = dict(reviewed)
    for c in cands:
        out[c.rel] = {"size": c.size, "mtime": c.mtime, "reviewed": today}
    return out


# ── Selection ────────────────────────────────────────────────────────

def parse_selection(text: str, n: int) -> list[int]:
    """Parse ``"1 3 5-7"`` (commas also accepted) into 0-based indices into a list of
    ``n`` rows. Raises ValueError naming the bad token."""
    picked: list[int] = []
    for tok in text.replace(",", " ").split():
        if "-" in tok:
            lo_s, _, hi_s = tok.partition("-")
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"bad range {tok!r}")
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                lo, hi = hi, lo
        elif tok.isdigit():
            lo = hi = int(tok)
        else:
            raise ValueError(f"bad number {tok!r}")
        if lo < 1 or hi > n:
            raise ValueError(f"{tok} is out of range (1-{n})")
        picked.extend(range(lo - 1, hi))
    return picked


# ── Sidecars and leftovers ───────────────────────────────────────────

def _stem_matches(name: str, stem: str) -> bool:
    return name.startswith(stem + ".") or name.startswith(stem + "-")


def sidecar_names(video_name: str, sibling_names: list[str],
                  is_dir: Callable[[str], bool]) -> list[str]:
    """Names among ``sibling_names`` (same folder) that belong to ``video_name``:
    ``Movie.en.srt``, ``Movie.nfo``, ``Movie-thumb.jpg``, ``Movie.trickplay``.
    ``is_dir(name)`` says whether a sibling is a folder.

    Only metadata/artwork/subtitle types qualify (``is_leftover``), so an unrelated
    ``Movie.zip`` never rides along. Other videos are never sidecars, and a sibling is
    assigned to the video with the *longest* matching stem — so
    ``Show S01E01-E02.nfo`` belongs to the double episode, not to ``Show S01E01.mkv``,
    even though both stems prefix it.
    """
    stem = PurePosixPath(video_name).stem
    videos = [s for s in sibling_names if PurePosixPath(s).suffix.lower() in VIDEO_EXTS]
    video_stems = [PurePosixPath(v).stem for v in videos]
    out: list[str] = []
    for name in sibling_names:
        if name == video_name or name in videos or not _stem_matches(name, stem):
            continue
        if not is_leftover(name, is_dir(name)):
            continue
        owners = [s for s in video_stems if _stem_matches(name, s)]
        if max(owners, key=len) == stem:
            out.append(name)
    return out


def is_leftover(name: str, is_dir: bool) -> bool:
    """Whether a file or folder may be swept away with the folder that holds it once
    that folder has no videos left — Jellyfin metadata, artwork and subtitles only."""
    if is_dir:
        return name.lower() in LEFTOVER_DIR_NAMES or name.lower().endswith(LEFTOVER_DIR_SUFFIXES)
    return PurePosixPath(name).suffix.lower() in LEFTOVER_FILE_EXTS
