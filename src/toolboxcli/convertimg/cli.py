"""
convertimg — Batch-convert all images in the current folder to a target format using ImageMagick.

Requires:
    magick (ImageMagick 7)

Usage:
    convertimg <format>
    convertimg webp
    convertimg -q 90 jpg
    convertimg -k png
    convertimg -n avif
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from toolboxcli._common.console import die, error, info, ok, warn
from toolboxcli._common.tooling import require_tool
from toolboxcli._common.trash import move_to_trash

IMAGE_EXTS = {
    "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif",
    "webp", "avif", "heic", "heif", "ico", "svg",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convertimg",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("format", help="Target format extension (e.g. jpg, png, webp, avif, tiff)")
    parser.add_argument(
        "-q", "--quality", type=int, default=85, metavar="N",
        help="Compression quality 1-100 (default: 85; only for lossy formats)",
    )
    parser.add_argument("-k", "--keep", action="store_true", help="Keep original files (default: trash them after successful conversion)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show what would be converted without doing anything")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("magick")

    target_fmt = args.format.lstrip(".").lower()

    if not 1 <= args.quality <= 100:
        die("--quality must be a number between 1 and 100")

    cwd = Path.cwd()
    images = sorted(
        p for p in cwd.iterdir()
        if p.is_file() and p.suffix.lstrip(".").lower() in IMAGE_EXTS
    )

    if not images:
        warn("No images found in the current directory.")
        return

    to_convert = []
    for img in images:
        if img.suffix.lstrip(".").lower() == target_fmt:
            info(f"Skipping (already {target_fmt}): {img.name}")
            continue
        to_convert.append(img)

    if not to_convert:
        warn(f"All images are already in {target_fmt} format.")
        return

    if args.dry_run:
        warn("Dry-run mode — no files will be changed.")

    converted = 0
    failed = 0

    for src in to_convert:
        dest = src.with_suffix(f".{target_fmt}")

        if args.dry_run:
            info(f"[dry-run] {src.name}  →  {dest.name}")
            continue

        if dest.exists() and dest != src:
            warn(f"Skipping '{src.name}': destination '{dest.name}' already exists")
            continue

        result = subprocess.run(
            ["magick", str(src), "-quality", str(args.quality), str(dest)],
            stderr=subprocess.DEVNULL,
        )

        if result.returncode == 0:
            ok(f"{src.name}  →  {dest.name}")
            converted += 1
            if not args.keep and dest != src:
                move_to_trash(str(src))
        else:
            error(f"Failed to convert: {src.name}")
            failed += 1
            if dest.exists():
                dest.unlink()

    if args.dry_run:
        return

    ok(f"Done. Converted: {converted}  |  Failed: {failed}")


if __name__ == "__main__":
    main()
