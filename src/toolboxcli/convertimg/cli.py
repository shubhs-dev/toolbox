"""
convertimg — Batch-convert all images in the current folder to a target format using ImageMagick.

Requires:
    magick (ImageMagick 7)

Usage:
    convertimg <format>
    convertimg webp
    convertimg -q 90 jpg
    convertimg -f png jpg          # only convert PNGs to JPG
    convertimg -f png -f webp jpg  # convert PNGs and WebPs to JPG
    convertimg -b black -f png jpg # fill transparent areas with black instead of white
    convertimg -k png
    convertimg -n avif

When the target format has no transparency support (jpg, bmp), transparent areas are
flattened onto the --background colour (white by default) instead of turning black.
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

# Spellings of the same format, so "jpg" matches .jpeg files and vice versa.
EXT_ALIASES = {"jpeg": "jpg", "tif": "tiff", "heif": "heic"}

# Target formats that can't store an alpha channel.
NO_ALPHA_FORMATS = {"jpg", "bmp"}


def normalize_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return EXT_ALIASES.get(ext, ext)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convertimg",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("format", help="Target format extension (e.g. jpg, png, webp, avif, tiff)")
    parser.add_argument(
        "-f", "--from", dest="sources", action="append", metavar="EXT",
        help="Only convert images with this extension (repeatable; default: all image types)",
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=85, metavar="N",
        help="Compression quality 1-100 (default: 85; only for lossy formats)",
    )
    parser.add_argument(
        "-b", "--background", default="white", metavar="COLOR",
        help="Fill colour for transparent areas when the target has no alpha, e.g. jpg (default: white)",
    )
    parser.add_argument("-k", "--keep", action="store_true", help="Keep original files (default: trash them after successful conversion)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show what would be converted without doing anything")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("magick")

    target_fmt = args.format.lstrip(".").lower()
    target_norm = normalize_ext(target_fmt)

    if not 1 <= args.quality <= 100:
        die("--quality must be a number between 1 and 100")

    source_filter = None
    if args.sources:
        source_filter = {normalize_ext(s) for src in args.sources for s in src.split(",") if s.strip()}
        unknown = source_filter - {normalize_ext(e) for e in IMAGE_EXTS}
        if unknown:
            die(f"Unsupported --from format(s): {', '.join(sorted(unknown))}")

    cwd = Path.cwd()
    images = sorted(
        p for p in cwd.iterdir()
        if p.is_file()
        and p.suffix.lstrip(".").lower() in IMAGE_EXTS
        and (source_filter is None or normalize_ext(p.suffix) in source_filter)
    )

    if not images:
        if source_filter:
            warn(f"No {'/'.join(sorted(source_filter))} images found in the current directory.")
        else:
            warn("No images found in the current directory.")
        return

    to_convert = []
    for img in images:
        if normalize_ext(img.suffix) == target_norm:
            info(f"Skipping (already {target_fmt}): {img.name}")
            continue
        to_convert.append(img)

    if not to_convert:
        warn(f"All images are already in {target_fmt} format.")
        return

    if args.dry_run:
        warn("Dry-run mode — no files will be changed.")

    flatten = target_norm in NO_ALPHA_FORMATS
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

        cmd = ["magick", str(src)]
        if flatten:
            cmd += ["-background", args.background, "-alpha", "remove", "-alpha", "off"]
        cmd += ["-quality", str(args.quality), str(dest)]

        result = subprocess.run(cmd, stderr=subprocess.DEVNULL)

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
