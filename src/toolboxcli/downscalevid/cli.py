"""
downscalevid — Reduce a video's resolution using ffmpeg.

Scales down to a target resolution while preserving aspect ratio (unless an explicit
WIDTHxHEIGHT is given). Never upscales unless -f/--force is passed. Re-encodes video
(H.264 by default); audio is stream-copied unchanged.

Requires:
    ffmpeg, ffprobe

Usage:
    downscalevid movie.mkv -r 1440p
    downscalevid movie.mkv -r 1920x1080 -o movie.1080p.mkv
    downscalevid movie.mkv -r 720 -c h265
    downscalevid movie.mkv -r 1440p -f     # allow upscaling too
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from toolboxcli._common.console import die, info, ok, warn
from toolboxcli._common.tooling import require_tool

PRESET_HEIGHTS = {
    "8k": 4320,
    "4k": 2160,
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}

CODECS = {
    "h264": "libx264",
    "h265": "libx265",
}

_WXH_RE = re.compile(r"^(\d+)x(\d+)$", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"^(\d+)p?$", re.IGNORECASE)


def parse_resolution(spec: str) -> tuple[int | None, int]:
    """Parse a -r/--resolution spec into (width_or_None, height)."""
    key = spec.strip().lower()
    if key in PRESET_HEIGHTS:
        return None, PRESET_HEIGHTS[key]

    m = _WXH_RE.match(key)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = _HEIGHT_RE.match(key)
    if m:
        return None, int(m.group(1))

    die(
        f"invalid resolution '{spec}' — use a preset ({', '.join(PRESET_HEIGHTS)}), "
        "a height (e.g. 900), or WIDTHxHEIGHT (e.g. 1920x1080)"
    )


def probe_dimensions(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        die(f"ffprobe failed to read '{path.name}': {result.stderr.strip()}")

    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        die(f"no video stream found in '{path.name}'")

    return streams[0]["width"], streams[0]["height"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="downscalevid",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video_file", help="Input video file")
    parser.add_argument("output_file", nargs="?", default=None, help="Output file (default: auto-derived)")
    parser.add_argument(
        "-r", "--resolution", required=True, metavar="RES",
        help=f"Target resolution: preset ({', '.join(PRESET_HEIGHTS)}), a height, or WIDTHxHEIGHT",
    )
    parser.add_argument(
        "-c", "--codec", choices=sorted(CODECS), default="h264",
        help="Video codec to re-encode with (default: h264)",
    )
    parser.add_argument("-f", "--force", action="store_true", help="Allow upscaling (default: refuse)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("ffmpeg")
    require_tool("ffprobe")

    input_path = Path(args.video_file)
    if not input_path.is_file():
        die(f"file not found: {input_path}")

    target_width, target_height = parse_resolution(args.resolution)

    src_width, src_height = probe_dimensions(input_path)

    if target_width is not None:
        would_not_shrink = target_width * target_height >= src_width * src_height
    else:
        would_not_shrink = target_height >= src_height
    if would_not_shrink and not args.force:
        ok(
            f"'{input_path.name}' is already at or below the target resolution "
            f"({src_width}x{src_height}), skipping. Use -f/--force to upscale anyway."
        )
        return

    if args.output_file:
        output_path = Path(args.output_file)
    else:
        label = args.resolution.strip().lower() if args.resolution.strip().lower() in PRESET_HEIGHTS else (
            f"{target_width}x{target_height}" if target_width else f"{target_height}p"
        )
        output_path = input_path.with_suffix(f".{label}{input_path.suffix}")

    if output_path == input_path:
        die("output path must differ from input path")

    scale_expr = f"{target_width}:{target_height}" if target_width else f"-2:{target_height}"

    ffmpeg_args = [
        "-y",
        "-i", str(input_path),
        "-vf", f"scale={scale_expr}",
        "-c:v", CODECS[args.codec],
        "-crf", "18",
        "-preset", "medium",
        "-c:a", "copy",
        str(output_path),
    ]

    info(f"Input      : {input_path}  ({src_width}x{src_height})")
    info(f"Output     : {output_path}")
    info(f"Resolution : {args.resolution}")
    info(f"Codec      : {args.codec}")

    result = subprocess.run(["ffmpeg", *ffmpeg_args])
    if result.returncode != 0:
        if output_path.exists():
            output_path.unlink()
        die(f"ffmpeg failed with exit code {result.returncode}")

    ok(f"Done → {output_path}")


if __name__ == "__main__":
    main()
