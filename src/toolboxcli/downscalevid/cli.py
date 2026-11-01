"""
downscalevid — Reduce a video's resolution using ffmpeg, GPU-accelerated when possible.

Scales down to a target resolution while preserving aspect ratio (unless an explicit
WIDTHxHEIGHT is given). Never upscales unless -f/--force is passed. Re-encodes video
(H.264 by default); audio is stream-copied unchanged. Every audio and subtitle track is
kept, along with chapters and (for MKV output) attachments such as subtitle fonts.

The output is written to a temporary file next to the destination and only moved into
place once ffmpeg succeeds. It can never be the input file, and an existing file is never
overwritten unless -o/--overwrite is given, in which case the old file is moved to the
Trash first.

Encoding runs on the GPU when a supported one is available — NVIDIA (NVENC), AMD (AMF)
or Apple silicon (VideoToolbox) — and falls back to the CPU encoder otherwise. Override
with -g/--gpu or the DOWNSCALEVID_GPU env var.

Requires:
    ffmpeg, ffprobe

Usage:
    downscalevid movie.mkv -r 1440p
    downscalevid movie.mkv -r 1920x1080 movie.1080p.mkv
    downscalevid movie.mkv -r 720 -c h265
    downscalevid movie.mkv -r 1440p -g cpu   # force software encoding
    downscalevid movie.mkv -r 1440p -f       # allow upscaling too
    downscalevid movie.mkv -r 720 out.mkv -o # replace an existing out.mkv
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from toolboxcli._common.console import die, info, ok, warn
from toolboxcli._common.ffprobe import probe
from toolboxcli._common.encoders import (
    CODECS,
    GPU_CHOICES,
    Encoder,
    cpu_encoder,
    gpu_preference,
    select_encoder,
)
from toolboxcli._common.output import atomic_output, check_output_path
from toolboxcli._common.streams import keep_all_streams_args, main_video_streams
from toolboxcli._common.tooling import require_tool
from toolboxcli.downscalevid.core import PRESET_HEIGHTS, parse_resolution

GPU_ENV_VAR = "DOWNSCALEVID_GPU"


def probe_streams(path: Path) -> list[dict]:
    """ffprobe streams of *path*; dies if it can't be read or has no real video stream
    (cover art doesn't count — scaling a thumbnail isn't what was asked for)."""
    data = probe(path)
    if data is None:
        die(f"ffprobe could not read '{path.name}'")
    streams = data.get("streams", [])
    if not main_video_streams(streams):
        die(f"no video stream found in '{path.name}'")
    return streams


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
    parser.add_argument(
        "-g", "--gpu", choices=GPU_CHOICES, default=None,
        help="Encoder to use (default: auto — first available GPU, else CPU). "
             f"Env: {GPU_ENV_VAR}",
    )
    parser.add_argument("-f", "--force", action="store_true", help="Allow upscaling (default: refuse)")
    parser.add_argument(
        "-o", "--overwrite", action="store_true",
        help="Replace the output file if it exists (the old one is moved to the Trash)",
    )
    return parser


def run_ffmpeg(
    input_path: Path, output_path: Path, scale_expr: str, encoder: Encoder, stream_maps: list[str]
) -> int:
    # -y only ever overwrites atomic_output()'s own temp file (or a failed first attempt
    # at it), never a real destination — check_output_path() has already vetted that.
    result = subprocess.run([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i", str(input_path),
        "-vf", f"scale={scale_expr}",
        "-c", "copy",
        "-c:v", encoder.name, *encoder.args,
        *stream_maps,
        str(output_path),
    ])
    return result.returncode


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("ffmpeg")
    require_tool("ffprobe")

    input_path = Path(args.video_file)
    if not input_path.is_file():
        die(f"file not found: {input_path}")

    target_width, target_height = parse_resolution(args.resolution)

    streams = probe_streams(input_path)
    main_video = main_video_streams(streams)[0]
    src_width, src_height = main_video["width"], main_video["height"]

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

    check_output_path(input_path, output_path, args.overwrite, flag="-o/--overwrite")
    stream_maps = keep_all_streams_args(streams, output_path.suffix.lower())

    scale_expr = f"{target_width}:{target_height}" if target_width else f"-2:{target_height}"

    preference = args.gpu or gpu_preference(GPU_ENV_VAR)
    encoder = select_encoder(args.codec, preference)

    info(f"Input      : {input_path}  ({src_width}x{src_height})")
    info(f"Output     : {output_path}")
    info(f"Resolution : {args.resolution}")
    info(f"Codec      : {args.codec}")
    info(f"Encoder    : {encoder.label} ({encoder.name})")

    try:
        with atomic_output(output_path, args.overwrite) as tmp_path:
            returncode = run_ffmpeg(input_path, tmp_path, scale_expr, encoder, stream_maps)

            # A GPU we picked ourselves can still fail on a specific input (unsupported
            # pixel format, session limit, driver hiccup) — retry once on the CPU rather
            # than making the user rerun with -g cpu. An explicitly requested GPU is not
            # second-guessed.
            if returncode != 0 and encoder.is_hardware and preference == "auto":
                warn(f"{encoder.label} encode failed (exit {returncode}), retrying on CPU...")
                encoder = cpu_encoder(args.codec)
                info(f"Encoder    : {encoder.label} ({encoder.name})")
                returncode = run_ffmpeg(input_path, tmp_path, scale_expr, encoder, stream_maps)

            if returncode != 0:
                die(f"ffmpeg failed with exit code {returncode}")
    except KeyboardInterrupt:
        die("interrupted", code=130)

    ok(f"Done → {output_path}")


if __name__ == "__main__":
    main()
