"""
cybermod — Automatically install Cyberpunk 2077 mods from zip/rar/7z archives.

Extracts each archive, detects the mod's directory structure (or loose
.archive files), previews what will be installed, and copies it into your
Cyberpunk 2077 install directory. Existing files that would be overwritten
are flagged for confirmation. The original archive is sent to the Recycle
Bin/Trash after a successful install.

Requires:
    7z (7-Zip)

Usage:
    cybermod [options] [archive ...]
    cybermod                          # process all zip/rar/7z in cwd
    cybermod mod1.zip mod2.rar
    cybermod -g "D:/Games/Cyberpunk 2077" mod.zip
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, die, info, warn
from toolboxcli._common.tooling import require_tool
from toolboxcli.cybermod.core import InstallStatus, install_mod

DEFAULT_GAME_DIR = "/d/SteamLibrary/steamapps/common/Cyberpunk 2077"
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cybermod",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("archives", nargs="*", help="Archive files to install (default: all zip/rar/7z in cwd)")
    parser.add_argument(
        "-g", "--game-dir",
        default=os.environ.get("CYBERMOD_GAME_DIR", DEFAULT_GAME_DIR),
        help="Cyberpunk 2077 install directory (env: CYBERMOD_GAME_DIR)",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview actions without installing")
    return parser


def collect_files(archives: list[str]) -> list[Path]:
    if archives:
        paths = [Path(a) for a in archives]
    else:
        paths = [p for p in Path.cwd().iterdir() if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS]
    return sorted(paths, key=lambda p: p.stat().st_mtime)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("7z", install_hint="install 7-Zip and add it to PATH")

    game_dir = Path(args.game_dir)
    if not game_dir.is_dir():
        die(
            f"Game directory not found: {game_dir}\n"
            "Pass -g/--game-dir, set CYBERMOD_GAME_DIR, or edit the default in cybermod/cli.py."
        )

    files = collect_files(args.archives)

    if not files:
        warn(f"No archive files found in {Path.cwd()}")
        console.print("Usage: cybermod [file.zip ...]")
        console.print("       Or run in a directory containing mod archives.")
        return

    console.print()
    info(f"Found {len(files)} archive(s) to install")
    info(f"Game directory: {game_dir}")

    def confirm_fn(prompt: str) -> bool:
        return confirm(prompt, choices="yn") == "y"

    total = len(files)
    success = fail = skipped = 0

    for idx, f in enumerate(files, start=1):
        status = install_mod(f, idx, total, game_dir, args.yes, args.dry_run, confirm_fn)
        if status == InstallStatus.SUCCESS:
            success += 1
        elif status == InstallStatus.SKIPPED:
            skipped += 1
        else:
            fail += 1

    console.print(f"\n[green]{'━' * 42}[/green]")
    info(
        f"Done! [green]{success} installed[/green], "
        f"[yellow]{skipped} skipped[/yellow], [red]{fail} failed[/red] (out of {total})"
    )


if __name__ == "__main__":
    main()
