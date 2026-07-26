"""
flipvid — Flip a video horizontally or vertically using ffmpeg.

Requires:
    ffmpeg

Usage:
    flipvid -x movie.mp4                 # flip horizontally (mirror)
    flipvid -y movie.mp4                 # flip vertically (upside down)
    flipvid -x -y movie.mp4 out.mp4      # flip both axes
    flipvid -x -q 20 movie.mp4           # custom encode quality

Flipping re-encodes the video stream (filters can't stream-copy); audio is
always stream-copied unchanged. Output filename is auto-derived as
<stem>.flip.<h|v|hv>.<ext> if not given.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from toolboxcli._common.console import die, info, ok
from toolboxcli._common.tooling import require_tool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flipvid",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video_file", help="Input video file")
    parser.add_argument("output_file", nargs="?", default=None, help="Output file (default: auto-derived)")
    parser.add_argument("-x", "--horizontal", action="store_true", help="Flip horizontally (mirror left-right)")
    parser.add_argument("-y", "--vertical", action="store_true", help="Flip vertically (upside down)")
    parser.add_argument("-q", "--quality", metavar="CRF", type=int, default=18,
                         help="x264 CRF quality, lower is better (default: 18)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("ffmpeg")

    if not args.horizontal and not args.vertical:
        die("specify at least one of -x/--horizontal or -y/--vertical")

    input_path = Path(args.video_file)
    if not input_path.is_file():
        die(f"file not found: {input_path}")

    filters = []
    tag = ""
    if args.horizontal:
        filters.append("hflip")
        tag += "h"
    if args.vertical:
        filters.append("vflip")
        tag += "v"

    if args.output_file:
        output_path = Path(args.output_file)
    else:
        output_path = input_path.with_suffix(f".flip.{tag}{input_path.suffix}")

    if output_path == input_path:
        die("output path must differ from input path")

    ffmpeg_args = [
        "-y",
        "-i", str(input_path),
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-crf", str(args.quality), "-preset", "fast",
        "-c:a", "copy",
        str(output_path),
    ]

    info(f"Input  : {input_path}")
    info(f"Output : {output_path}")
    info(f"Flip   : {'horizontal' if args.horizontal else ''}"
         f"{' + ' if args.horizontal and args.vertical else ''}"
         f"{'vertical' if args.vertical else ''}")

    result = subprocess.run(["ffmpeg", *ffmpeg_args])
    if result.returncode != 0:
        if output_path.exists():
            output_path.unlink()
        die(f"ffmpeg failed with exit code {result.returncode}")

    ok(f"Done → {output_path}")


if __name__ == "__main__":
    main()
