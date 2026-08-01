"""
sortmedia — Move video files into folders based on the camelCase type tag in each filename.

For each video file in the current directory, the last space/underscore/hyphen/dot-separated
segment of the filename stem is treated as a camelCase type identifier (e.g. `elephantHerd`
-> `ElephantHerd`, `ATCRecording` -> `ATCRecording`) with only its first letter capitalized.
The base folder ("Location A" -- passed as an argument, or prompted for interactively) is
searched recursively for a matching subfolder (case-insensitive); if found, the video is
moved there, otherwise you're prompted to create it.

Usage:
    sortmedia
    sortmedia ~/Videos/LocationA
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, die, info, ok, warn

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm", ".wmv"}

_MSYS_PATH_RE = re.compile(r"^/([a-zA-Z])(/.*)?$")


def _normalize_path(raw: str) -> str:
    """Convert a Git-Bash/MSYS2-style path (/d/foo) to a native Windows path (D:\\foo)."""
    m = _MSYS_PATH_RE.match(raw)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\")
        return f"{drive}:{rest}"
    return raw


def extract_type(stem: str) -> str | None:
    parts = [p for p in re.split(r"[ _\-.]+", stem) if p]
    return parts[-1] if parts else None


def capitalize_type(word: str) -> str:
    return word[:1].upper() + word[1:] if word else word


def find_folder(base: Path, name: str) -> Path | None:
    name_lower = name.lower()
    for dirpath, dirnames, _filenames in os.walk(base):
        for d in dirnames:
            if d.lower() == name_lower:
                return Path(dirpath) / d
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sortmedia",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("location_a", nargs="?", default=None, help="Base folder to search for destination folders")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    raw = args.location_a
    if raw is None:
        raw = console.input("Location A (base folder to search for destination folders): ")
    raw = raw.strip().strip('"').strip("'")
    raw = _normalize_path(raw)

    location_a = Path(raw)
    if not location_a.is_dir():
        die(f"not a directory: {location_a}")

    videos = sorted(p for p in Path.cwd().iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)

    moved = 0
    skipped = 0

    for video in videos:
        type_tag = extract_type(video.stem)
        if not type_tag:
            warn(f"Skipping (no type tag): {video.name}")
            skipped += 1
            continue

        folder_name = capitalize_type(type_tag)
        dest_folder = find_folder(location_a, folder_name)

        if dest_folder is None:
            if confirm(f"No folder matching '{folder_name}' found. Create it?", choices="yn", default="n") != "y":
                info(f"Skipped: {video.name}")
                skipped += 1
                continue
            dest_folder = location_a / folder_name
            dest_folder.mkdir(parents=True, exist_ok=True)

        dest_file = dest_folder / video.name
        if dest_file.exists():
            warn(f"Already exists, skipping: {dest_file}")
            skipped += 1
            continue

        shutil.move(str(video), str(dest_file))
        ok(f"Moved: {video.name} -> {dest_folder}")
        moved += 1

    console.print()
    info(f"Moved: {moved}  Skipped: {skipped}")


if __name__ == "__main__":
    main()
