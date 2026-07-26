"""
finddupes — Find duplicate filenames across a directory tree.

Scans <directory> (default: current directory) and all its subdirectories,
builds a list of every filename, and reports any names that appear more
than once along with their full paths.

Usage:
    finddupes [options] [directory]
    finddupes -c -i ~/Documents
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from toolboxcli._common.console import console, die, ok, warn
from toolboxcli._common.progress import spinner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finddupes",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target_dir", nargs="?", default=".", help="Directory to scan (default: .)")
    parser.add_argument("-c", "--count", action="store_true", help="Sort results by duplicate count (highest first)")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="Case-insensitive filename comparison")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    target_dir = Path(args.target_dir)
    if not target_dir.is_dir():
        die(f"not a directory: {target_dir}")

    index: dict[str, list[Path]] = defaultdict(list)
    total = 0

    with spinner(transient=True) as progress:
        task = progress.add_task(f"Scanning {target_dir} …", total=None)
        for p in target_dir.rglob("*"):
            if p.is_file():
                total += 1
                key = p.name.lower() if args.ignore_case else p.name
                index[key].append(p)
                if total % 50 == 0:
                    progress.update(task, description=f"Indexed {total} files …")

    ok(f"Indexed {total} files")

    dupes = {k: v for k, v in index.items() if len(v) > 1}
    if not dupes:
        ok("No duplicate filenames found.")
        return

    warn(f"Found {len(dupes)} duplicate filename(s):")
    console.print()

    if args.count:
        items = sorted(dupes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    else:
        items = sorted(dupes.items(), key=lambda kv: kv[0])

    for key, paths in items:
        console.print(f"[yellow]{key}[/yellow]  ({len(paths)} copies)")
        for p in paths:
            console.print(f"  {p}")
        console.print()


if __name__ == "__main__":
    main()
