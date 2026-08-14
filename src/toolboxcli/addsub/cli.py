"""
addsub — Merge a subtitle file into a video file using ffmpeg.

Requires:
    ffmpeg

Usage:
    addsub [options] <video_file> <subtitle_file> [output_file]
    addsub movie.mkv movie.srt
    addsub movie.mp4 movie.ass output.mp4
    addsub -b movie.mp4 movie.srt burned.mp4
    addsub -l jpn -t "Japanese" movie.mkv movie.ass
    addsub -u movie.mkv movie.srt
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from toolboxcli._common.console import console, die
from toolboxcli._common.tooling import require_tool
from toolboxcli._common.trash import move_to_trash

ASS_EXTS = {"ass", "ssa"}


def _run_ffmpeg(cmd: list[str]) -> int:
    """Run ffmpeg, streaming its stderr through with libdvdread's harmless DVD-probe
    noise dropped (it writes straight to stderr on every input, DVD or not, and
    ignores ffmpeg's own -loglevel)."""
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, bufsize=1)
    for line in proc.stderr:
        if not line.lstrip().startswith("libdvdread:"):
            sys.stderr.write(line)
    return proc.wait()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="addsub",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video_file", help="Input video file")
    parser.add_argument("subtitle_file", help="Subtitle file to merge")
    parser.add_argument(
        "output_file", nargs="?", default=None,
        help="Output file (default: update video in place)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-s", "--soft", action="store_const", dest="mode", const="soft",
                       help="Mux subtitles as a selectable stream (default)")
    mode.add_argument("-b", "--hard", action="store_const", dest="mode", const="hard",
                       help="Burn subtitles into the video (not reversible)")
    parser.set_defaults(mode="soft")
    parser.add_argument("-l", "--lang", default="eng", help="Language code for the subtitle track (default: eng)")
    parser.add_argument("-t", "--title", default=None, help="Title for the subtitle track (default: subtitle filename)")
    parser.add_argument(
        "-u", "--suffix", action="store_true",
        help="Append ' - Sub' to the output filename instead of updating in place "
        "(ignored if output_file is given)",
    )
    parser.add_argument(
        "-k", "--keep", action="store_true",
        help="Keep the original video file (default: trash it when output differs)",
    )
    return parser


def _escape_for_filtergraph(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:")


def _sub_codec(out_ext: str, sub_ext: str) -> str:
    if out_ext in ("mp4", "m4v"):
        return "mov_text"
    if out_ext in ("mkv", "webm"):
        return "copy" if sub_ext in ASS_EXTS else "srt"
    return "copy"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("ffmpeg")

    video = Path(args.video_file)
    subtitle = Path(args.subtitle_file)

    if not video.is_file():
        die(f"video file not found: {video}")
    if not subtitle.is_file():
        die(f"subtitle file not found: {subtitle}")

    sub_ext = subtitle.suffix.lstrip(".").lower()
    track_title = args.title or subtitle.name or "Subtitle"

    tmpfile: Path | None = None
    if args.output_file is not None:
        output = Path(args.output_file)
        inplace = False
    elif args.suffix:
        output = video.parent / f"{video.stem} - Sub{video.suffix}"
        inplace = False
    else:
        video_ext = video.suffix.lstrip(".")
        fd, tmpname = tempfile.mkstemp(suffix=f".{video_ext}")
        os.close(fd)
        tmpfile = Path(tmpname)
        output = tmpfile
        inplace = True

    out_ext = output.suffix.lstrip(".").lower()

    console.print(f"addsub: merging '{subtitle}' → '{video}'")
    console.print(f"  mode   : {args.mode}")
    console.print(f"  lang   : {args.lang}")
    console.print(f"  output : {output}")
    console.print()

    try:
        if args.mode == "hard":
            safe_sub = _escape_for_filtergraph(str(subtitle))
            vf_filter = f"ass={safe_sub}" if sub_ext in ASS_EXTS else f"subtitles={safe_sub}"
            cmd = [
                "ffmpeg", "-y", "-i", str(video),
                "-vf", vf_filter,
                "-c:v", "libx264", "-crf", "18", "-preset", "slow",
                "-c:a", "copy",
                str(output),
            ]
        else:
            sub_codec = _sub_codec(out_ext, sub_ext)
            cmd = [
                "ffmpeg", "-y", "-i", str(video), "-i", str(subtitle),
                "-map", "0", "-map", "1",
                "-c", "copy",
                "-c:s", sub_codec,
                "-metadata:s:s:0", f"language={args.lang}",
                "-metadata:s:s:0", f"title={track_title}",
                "-disposition:s:0", "default",
                str(output),
            ]

        returncode = _run_ffmpeg(cmd)
        if returncode != 0:
            die(f"ffmpeg failed with exit code {returncode}")

        console.print()
        if inplace:
            tmpfile.replace(video)
            tmpfile = None
            console.print(f"addsub: done → {video} (updated in place)")
        else:
            console.print(f"addsub: done → {output}")
            if not args.keep:
                move_to_trash(str(video))
                console.print(f"addsub: trashed original video → {video}")
    finally:
        if tmpfile is not None and tmpfile.exists():
            tmpfile.unlink()

    move_to_trash(str(subtitle))
    console.print(f"addsub: trashed subtitle → {subtitle}")


if __name__ == "__main__":
    main()
