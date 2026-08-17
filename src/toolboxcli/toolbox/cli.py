"""
toolbox — List all custom scripts in this repo with a brief description of each.

Usage:
    toolbox
"""

from __future__ import annotations

import argparse

from rich.table import Table
from rich import box

from toolboxcli._common.console import console

SCRIPTS = [
    ("addsub", "Merge a subtitle file into a video (soft-mux or hard-burn) via ffmpeg"),
    ("autosub", "Auto-match subtitle files to videos by episode code and run addsub"),
    ("check-deps", "Scan subdirectories for specific npm package versions"),
    ("compressvid", "Watch a folder for videos and transcode them with HandBrake; keeps the smaller copy"),
    ("concatvid", "Concatenate split video parts in a folder using ffmpeg (stream-copy where possible)"),
    ("convertimg", "Batch-convert all images in the current folder to a target format via ImageMagick"),
    ("cutvid", "Trim a video to a start/end time using ffmpeg (stream-copy or re-encode)"),
    ("cybermod", "Extract and install Cyberpunk 2077 mods from zip/rar/7z archives"),
    ("downscalevid", "Reduce a video's resolution using ffmpeg, GPU-accelerated when available (never upscales)"),
    ("finddupes", "Find duplicate filenames across a directory tree"),
    ("flatten", "Move all files from subdirectories up into the current directory"),
    ("flipvid", "Flip a video horizontally or vertically using ffmpeg"),
    ("jellyname", "Rename and organize media files into a Jellyfin-compatible folder structure"),
    ("kavitaname", "Rename manga/manhwa/book files for Kavita server compatibility"),
    ("mergemanga", "Merge One Piece chapter CBZ files into volume CBZ files with metadata"),
    ("optimiselib", "Watch a library root: compress new videos to 1080p, tag the resolution and sort by trip"),
    ("sortmedia", "Move video files into folders based on the camelCase type tag in each filename"),
    ("toolbox", "List all custom scripts with a brief description (this script)"),
]


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="toolbox",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def main() -> None:
    build_parser().parse_args()

    table = Table(title="Toolbox — Custom Scripts", box=box.ROUNDED, title_style="bold yellow")
    table.add_column("Script", style="green")
    table.add_column("Description")
    for name, desc in SCRIPTS:
        table.add_row(name, desc)

    console.print()
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
