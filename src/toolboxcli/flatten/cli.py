"""
flatten — Recursively move all files from subdirectories into the current directory.

Scans all subdirectories under the current directory and moves every file
up into the directory where the command was invoked. Empty subdirectories
are removed after flattening.

When a filename conflict occurs (and --yes is not set), you are prompted
with size, modification time, and path info for both files so you can
decide whether to replace.

Usage:
    flatten [options]
    flatten --yes
    flatten --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, info, ok, warn
from toolboxcli._common.humanize import human_size


def file_info(p: Path) -> str:
    st = p.stat()
    mod = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"{mod}  ({human_size(st.st_size)})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flatten",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Replace all conflicting files without prompting")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show what would be moved without making changes")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dest = Path.cwd()
    info(f"Scanning subdirectories of {dest} …")

    files = sorted(p for p in dest.rglob("*") if p.is_file() and p.parent != dest)

    moved = replaced = skipped = 0
    replace_all = args.yes

    for src in files:
        dest_file = dest / src.name

        if args.dry_run:
            if dest_file.exists():
                warn(f"[dry-run] Would conflict: {src}")
            else:
                info(f"[dry-run] Would move: {src}")
            continue

        if not dest_file.exists():
            shutil.move(str(src), str(dest_file))
            moved += 1
            continue

        if replace_all:
            shutil.move(str(src), str(dest_file))
            replaced += 1
            continue

        console.print()
        console.print(f"[bold]Conflict:[/bold] [yellow]{src.name}[/yellow]")
        console.print(f"  [cyan]Existing:[/cyan] {dest_file}")
        console.print(f"           {file_info(dest_file)}")
        console.print(f"  [cyan]Incoming:[/cyan] {src}")
        console.print(f"           {file_info(src)}")

        reply = confirm("Replace?", choices="yna")
        if reply == "a":
            replace_all = True
            reply = "y"
        if reply == "y":
            shutil.move(str(src), str(dest_file))
            replaced += 1
        else:
            skipped += 1

    if not args.dry_run:
        for dirpath, _dirnames, _filenames in os.walk(dest, topdown=False):
            d = Path(dirpath)
            if d == dest:
                continue
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

    console.print()
    if args.dry_run:
        info(f"Dry run complete. {len(files)} file(s) found in subdirectories.")
    else:
        ok(f"Done. {moved} moved, {replaced} replaced, {skipped} skipped.")
        remaining = sum(1 for d in dest.rglob("*") if d.is_dir())
        if remaining:
            warn(f"{remaining} non-empty subdirectory(ies) remain.")


if __name__ == "__main__":
    main()
