"""
flipvid — Flip a video horizontally or vertically using ffmpeg.

Requires:
    ffmpeg, ffprobe

Usage:
    flipvid -x movie.mp4                 # flip horizontally (mirror)
    flipvid -y movie.mp4                 # flip vertically (upside down)
    flipvid -x -y movie.mp4 out.mp4      # flip both axes
    flipvid -x -q 20 movie.mp4           # custom encode quality
    flipvid -x -f movie.mp4 out.mp4      # replace an existing out.mp4

Flipping re-encodes the video stream (filters can't stream-copy); audio is
always stream-copied unchanged. Every audio and subtitle track is kept, along
with chapters and (for MKV output) attachments such as subtitle fonts.

The output is written to a temporary file next to the destination and only moved
into place once ffmpeg succeeds. It can never be the input file, and an existing
file is never overwritten unless -f/--force is given, in which case the old file
is moved to the Trash first.

Output filename is auto-derived as <stem>.flip.<h|v|hv>.<ext> if not given.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from toolboxcli._common.console import die, info, ok
from toolboxcli._common.ffprobe import probe
from toolboxcli._common.output import atomic_output, check_output_path
from toolboxcli._common.streams import keep_all_streams_args, main_video_streams
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
    parser.add_argument("-f", "--force", action="store_true",
                         help="Replace the output file if it exists (the old one is moved to the Trash)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("ffmpeg")
    require_tool("ffprobe")

    if not args.horizontal and not args.vertical:
        die("specify at least one of -x/--horizontal or -y/--vertical")

    input_path = Path(args.video_file)
    if not input_path.is_file():
        die(f"file not found: {input_path}")

    data = probe(input_path)
    if data is None:
        die(f"ffprobe could not read {input_path}")
    streams = data.get("streams", [])
    if not main_video_streams(streams):
        die(f"no video stream found in {input_path}")

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
        output_path = input_path.with_name(f"{input_path.stem}.flip.{tag}{input_path.suffix}")

    check_output_path(input_path, output_path, args.force)

    ffmpeg_args = [
        "-hide_banner",
        "-i", str(input_path),
        "-vf", ",".join(filters),
        "-c", "copy",
        "-c:v", "libx264", "-crf", str(args.quality), "-preset", "fast",
        *keep_all_streams_args(streams, output_path.suffix.lower()),
    ]

    info(f"Input  : {input_path}")
    info(f"Output : {output_path}")
    info(f"Flip   : {'horizontal' if args.horizontal else ''}"
         f"{' + ' if args.horizontal and args.vertical else ''}"
         f"{'vertical' if args.vertical else ''}")

    try:
        with atomic_output(output_path, args.force) as tmp_path:
            result = subprocess.run(["ffmpeg", "-y", *ffmpeg_args, str(tmp_path)])
            if result.returncode != 0:
                die(f"ffmpeg failed with exit code {result.returncode}")
    except KeyboardInterrupt:
        die("interrupted", code=130)

    ok(f"Done → {output_path}")


if __name__ == "__main__":
    main()
