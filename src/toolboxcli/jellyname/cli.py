"""
jellyname — Rename and organize media files for Jellyfin compatibility.

Scans [directory] (top level only) for video files, parses each filename to
extract title, year, season/episode, and media-info tags, then renames and
moves each file into a Jellyfin-compatible folder structure inside
<directory>.

Output structure:
    Movies:   Movie Name (Year)/Movie Name (Year) [tags] - 1080p.mkv
    TV/Anime: Show Name/Season 01/Show Name S01E01 [tags] - 720p.mkv

Resolution is preserved as a Jellyfin version-label suffix ( - 1080p), since
that's the only tag Jellyfin itself understands (it sorts multiple versions
of the same title by resolution). Other detected media info — source/edition
(BluRay, WEB-DL, REMUX, IMAX, ...), video codec/bit-depth (x265, 10bit, ...),
HDR/Dolby Vision variant, and audio codec (Atmos, DTS, DDP5.1, ...) — is
preserved in a bracketed suffix instead of being discarded. Unrecognized
tokens (release-group names, hashes, fansub tags) are still dropped.
Multi-episode files are supported (S01E01-E02). Characters illegal in
Jellyfin paths (< > : " / \\ | ? *) are removed.

jellyname always reads the actual file via ffprobe and fills in whichever
resolution, video codec, bit-depth, HDR, or audio-codec tags the filename
left out — tags already present in the filename are never overridden.
Source/edition (BluRay, PROPER, IMAX, ...) describe release provenance
rather than stream properties, so they're never derived from the decoded
video/audio itself — but when the file's container has an embedded title
tag (common for scene releases and disc rips, and untouched by renaming the
file), it's parsed the same way a filename is and used to fill in any
source/edition tags still missing. ffprobe (part of ffmpeg) must be
installed to run jellyname.

If no directory is given, the current working directory is used.

Usage:
    jellyname [options] [directory]
    jellyname
    jellyname ~/Downloads/movies
    jellyname -n /mnt/media/shows
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from toolboxcli._common import ffprobe as ffprobe_common
from toolboxcli._common.console import console, die, info, ok, warn
from toolboxcli._common.tooling import require_tool
from toolboxcli.jellyname.core import VIDEO_EXTS, MediaTags, apply_probed_tags, probed_tags, process_file


def do_rename(src_file: Path, target_dir: Path, new_filename: str, src_dir: Path, dry_run: bool) -> str:
    """Returns 'renamed' or 'skipped'."""
    target_path = target_dir / new_filename

    if target_path.exists() and src_file.resolve() == target_path.resolve():
        info(f"Already correct: {new_filename}")
        return "skipped"

    if dry_run:
        try:
            rel_target = target_dir.relative_to(src_dir)
            target_display = f"{rel_target}/{new_filename}"
        except ValueError:
            target_display = f"{target_dir}/{new_filename}"
        console.print(f"  [cyan]{src_file.name}[/cyan]")
        console.print(f"  [bold]→[/bold] [green]{target_display}[/green]")
        console.print()
        return "renamed"

    if target_path.exists():
        warn(f"Skipping (destination exists): {target_path}")
        return "skipped"

    target_dir.mkdir(parents=True, exist_ok=True)
    src_file.rename(target_path)
    ok(new_filename)
    return "renamed"


def make_tag_augmenter() -> Callable[[Path, MediaTags], None]:
    """ffprobe-backed fallback for tag categories the filename left out.

    Always probes — a fully-tagged filename still can't rule out bit-depth or
    HDR the release group left unstated. `require_tool` is checked lazily on
    first actual use so the error, if ffprobe is missing, surfaces once you'd
    actually need it rather than at import time.
    """
    checked_tool = False

    def augment_tags(filepath: Path, tags: MediaTags) -> None:
        nonlocal checked_tool
        if not checked_tool:
            require_tool("ffprobe", "part of ffmpeg")
            checked_tool = True
        payload = ffprobe_common.probe(filepath)
        if payload is None:
            warn(f"Could not read media info via ffprobe: {filepath.name}")
            return
        apply_probed_tags(tags, probed_tags(payload))

    return augment_tags


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jellyname",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("directory", nargs="?", default=".", help="Directory to scan (default: current directory)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview what would be renamed without moving any files")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    src_dir = Path(args.directory)
    if not src_dir.is_dir():
        die(f"Directory not found: {src_dir}")
    src_dir = src_dir.resolve()

    if args.dry_run:
        info("Dry run — no files will be moved.")
        console.print()

    info(f"Scanning: {src_dir}")

    files = sorted(
        p for p in src_dir.iterdir()
        if p.is_file() and p.suffix.lstrip(".").lower() in VIDEO_EXTS
    )

    if not files:
        warn(f"No video files found in {src_dir}")
        return

    info(f"Found {len(files)} video file(s).")
    if args.dry_run:
        console.print()

    renamed = 0
    skipped = 0
    augment_tags = make_tag_augmenter()

    for filepath in files:
        parsed = process_file(filepath, src_dir, augment_tags)
        if parsed is None:
            skipped += 1
            continue
        target_dir, new_filename = parsed
        result = do_rename(filepath, target_dir, new_filename, src_dir, args.dry_run)
        if result == "renamed":
            renamed += 1
        else:
            skipped += 1

    console.print()
    if args.dry_run:
        info(f"Dry run complete — {renamed} file(s) would be renamed, {skipped} skipped.")
    else:
        ok(f"Done — {renamed} renamed, {skipped} skipped.")


if __name__ == "__main__":
    main()
