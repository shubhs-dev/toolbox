"""
cutvid — Trim a video to a start/end time using ffmpeg.

Requires:
    ffmpeg

Usage:
    cutvid <video_file> [output_file]
    cutvid -s 00:01:30 -e 00:05:00 movie.mkv
    cutvid -s 90 -d 120 movie.mp4 clip.mp4
    cutvid -s 00:01:30 movie.mkv
    cutvid -e 00:05:00 movie.mkv
    cutvid -s 1:30 -e 5:00 -r movie.mkv clip.mkv

Stream-copy mode (default) is near-instant but cuts on keyframes, so the actual
start may be slightly before the requested time. Use -r/--reencode for
frame-accurate cuts. Output filename is auto-derived as <stem>.cut.<start>-<end>.<ext>
if not given.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from toolboxcli._common.console import die, info, ok
from toolboxcli._common.tooling import require_tool

_PLAIN_SECONDS_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def to_seconds(t: str) -> float:
    if _PLAIN_SECONDS_RE.match(t):
        return float(t)
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600 + float(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return float(m) * 60 + float(s)
    return float(parts[0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cutvid",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video_file", help="Input video file")
    parser.add_argument("output_file", nargs="?", default=None, help="Output file (default: auto-derived)")
    parser.add_argument("-s", "--start", metavar="TIME", default="",
                         help="Start time (default: beginning). Accepts HH:MM:SS, MM:SS, or seconds")
    parser.add_argument("-e", "--end", metavar="TIME", default="",
                         help="End time (default: end of file). Accepts HH:MM:SS, MM:SS, or seconds")
    parser.add_argument("-d", "--duration", metavar="DUR", default="",
                         help="Duration of the cut instead of end time")
    parser.add_argument("-r", "--reencode", action="store_true",
                         help="Re-encode output (slower, frame-accurate). Default: stream-copy")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("ffmpeg")

    if args.end and args.duration:
        die("--end and --duration are mutually exclusive")

    input_path = Path(args.video_file)
    if not input_path.is_file():
        die(f"file not found: {input_path}")

    if args.output_file:
        output_path = Path(args.output_file)
    else:
        suffix = f"{args.start or '0'}-{args.end or args.duration or 'end'}"
        suffix = suffix.replace(":", "-")
        output_path = input_path.with_suffix(f".cut.{suffix}{input_path.suffix}")

    if output_path == input_path:
        die("output path must differ from input path")

    ffmpeg_args = ["-y"]

    if args.start:
        ffmpeg_args += ["-ss", args.start]

    ffmpeg_args += ["-i", str(input_path)]

    if args.end:
        if args.start:
            # After input-seeking, ffmpeg resets output timestamps to 0, so -to would
            # act as a duration. Instead compute the true duration and use -t.
            dur = to_seconds(args.end) - to_seconds(args.start)
            if dur <= 0:
                die("--end must be after --start")
            ffmpeg_args += ["-t", f"{dur:.6f}"]
        else:
            # No seek — timestamps start at 0, so -to correctly refers to the file timeline.
            ffmpeg_args += ["-to", args.end]
    elif args.duration:
        ffmpeg_args += ["-t", args.duration]

    if args.reencode:
        ffmpeg_args += ["-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac"]
    else:
        ffmpeg_args += ["-c", "copy"]

    ffmpeg_args += [str(output_path)]

    info(f"Input  : {input_path}")
    info(f"Output : {output_path}")
    if args.start:
        info(f"Start  : {args.start}")
    if args.end:
        info(f"End    : {args.end}")
    if args.duration:
        info(f"Duration: {args.duration}")
    info(f"Mode   : {'re-encode' if args.reencode else 'stream-copy (fast)'}")

    result = subprocess.run(["ffmpeg", *ffmpeg_args])
    if result.returncode != 0:
        if output_path.exists():
            output_path.unlink()
        die(f"ffmpeg failed with exit code {result.returncode}")

    ok(f"Done → {output_path}")


if __name__ == "__main__":
    main()
