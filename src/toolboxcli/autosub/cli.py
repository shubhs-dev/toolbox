"""
autosub — Auto-match subtitle files to videos in the current directory by episode code,
then run `addsub -u` for each match.

The episode code is the 2nd ' - '-separated segment of the filename (e.g.
"Show - S01E01 - Title.mkv" -> "S01E01"). Video files are snapshotted upfront so files
created by addsub are not re-processed.

Requires:
    addsub (on PATH)

Usage:
    autosub
    autosub -y
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import error, info, ok, warn
from toolboxcli._common.tooling import require_tool

VIDEO_EXTS = {"mkv", "mp4", "avi", "mov", "m4v", "ts", "wmv"}
SUB_EXTS = {"srt", "ass", "ssa", "vtt", "sub"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autosub",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts and run addsub for all matches")
    return parser


def extract_code(filename: str) -> str | None:
    parts = filename.split(" - ")
    if len(parts) < 2:
        return None
    code = parts[1].strip()
    return code or None


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("addsub")

    cwd = Path.cwd()
    videos = sorted(
        p for p in cwd.iterdir()
        if p.is_file() and p.suffix.lstrip(".").lower() in VIDEO_EXTS
    )

    if not videos:
        warn("No video files found in the current directory.")
        return

    for video in videos:
        code = extract_code(video.name)
        if not code:
            warn(f"Skipping '{video.name}' — no ' - ' separator found, cannot extract code.")
            continue

        matches = sorted(
            p for p in cwd.iterdir()
            if p.is_file()
            and p.suffix.lstrip(".").lower() in SUB_EXTS
            and code.lower() in p.name.lower()
        )

        if not matches:
            info(f"No subtitle match for '{video.name}' (code: {code})")
            continue

        if len(matches) > 1:
            warn(f"Multiple subtitles matched '{video.name}' — using first match:")
            for m in matches:
                warn(f"  {m.name}")

        sub_file = matches[0]

        if not args.yes:
            if confirm(f'Run: addsub -u "{video.name}" "{sub_file.name}"?', choices="yn") != "y":
                info(f"Skipped: {video.name}")
                continue

        result = subprocess.run(["addsub", "-u", video.name, sub_file.name], cwd=cwd)
        if result.returncode == 0:
            ok(f"Processed: {video.name}")
        else:
            error(f"addsub failed for: {video.name}")

    ok("All done.")


if __name__ == "__main__":
    main()
