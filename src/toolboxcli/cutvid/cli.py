"""
cutvid — Trim a video to a start/end time using ffmpeg.

Requires:
    ffmpeg, ffprobe

Usage:
    cutvid <video_file> [output_file]
    cutvid -s 00:01:30 -e 00:05:00 movie.mkv
    cutvid -s 90 -d 120 movie.mp4 clip.mp4
    cutvid -s 00:01:30 movie.mkv
    cutvid -e 00:05:00 movie.mkv
    cutvid -s 1:30 -e 5:00 -r movie.mkv clip.mkv
    cutvid -s 1:30 -e 5:00 -f movie.mkv clip.mkv

Times accept HH:MM:SS, MM:SS or plain seconds, each with optional decimals
(1:02:03.5, 90.25).

Every video, audio and subtitle track is kept, along with chapters and (for MKV
output) attachments such as subtitle fonts. When cutting into a different container,
text subtitles are converted to one it supports (mov_text for MP4, SRT for MKV);
image subtitles that MP4 can't hold are dropped with a warning.

Stream-copy mode (default) is near-instant but can only start on a keyframe. MKV (and
most other) output starts at the last keyframe at or before the requested start —
cutvid warns you where that is when it differs. MP4/MOV output keeps the same
lead-in but hides it with an edit list, so it plays from the exact requested time.
The end point is unaffected. Use -r/--reencode for a frame-accurate start in any
container (video is re-encoded to H.264, audio to AAC; subtitles and attachments
are copied).

The output is written to a temporary file next to the destination and only moved
into place once ffmpeg succeeds. It can never be the input file, and an existing
file is never overwritten unless -f/--force is given, in which case the old file
is moved to the Trash first.

Output filename is auto-derived as <stem>.cut.<start>-<end>.<ext> if not given,
e.g. movie.cut.1m30s-5m00s.mkv.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from toolboxcli._common.console import die, info, ok, warn
from toolboxcli._common.ffprobe import probe
from toolboxcli._common.output import atomic_output, check_output_path
from toolboxcli._common.streams import MP4_EXTS, keep_all_streams_args, main_video_streams
from toolboxcli._common.tooling import require_tool

# [[HH:]MM:]SS[.frac] — digits only, so negative or suffixed values ("1m30s") are rejected.
_TIME_RE = re.compile(r"^(?:(?:([0-9]+):)?([0-9]+):)?([0-9]+(?:\.[0-9]+)?)$")

# Stream-copy starting this much earlier than requested is worth mentioning (~1 frame).
_KEYFRAME_NOTE_THRESHOLD = 0.05
# How far back from the requested start to look for a keyframe.
_KEYFRAME_SEARCH_WINDOW = 120.0


def parse_time(value: str, flag: str) -> float:
    m = _TIME_RE.match(value.strip())
    if not m:
        die(f"{flag}: invalid time '{value}' (use HH:MM:SS, MM:SS or seconds, e.g. 1:30 or 90)")
    hours, minutes, seconds = m.groups()
    secs = float(seconds)
    if minutes is not None and secs >= 60:
        die(f"{flag}: seconds must be below 60 in '{value}'")
    if hours is not None and int(minutes) >= 60:
        die(f"{flag}: minutes must be below 60 in '{value}'")
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + secs


def _split_ms(sec: float) -> tuple[int, int, int, int]:
    total_ms = round(sec * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return hours, minutes, seconds, millis


def format_clock(sec: float) -> str:
    """90.5 -> 00:01:30.500"""
    h, m, s, ms = _split_ms(sec)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def format_tag(sec: float) -> str:
    """Filename-safe time: 5 -> 5s, 90 -> 1m30s, 3723.5 -> 1h02m03.5s"""
    h, m, s, ms = _split_ms(sec)
    s_str = f"{s:02d}" if (h or m) else str(s)
    if ms:
        s_str += f".{ms:03d}".rstrip("0")
    if h:
        return f"{h}h{m:02d}m{s_str}s"
    if m:
        return f"{m}m{s_str}s"
    return f"{s_str}s"


def keyframe_at_or_before(path: Path, stream_index: int, t: float) -> float | None:
    """Timestamp of the last video keyframe at or before absolute time t, or None if
    there isn't one within the search window (or ffprobe fails)."""
    lo = max(0.0, t - _KEYFRAME_SEARCH_WINDOW)
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", str(stream_index),
                "-read_intervals", f"{lo:.6f}%{t + 1:.6f}",
                "-show_entries", "packet=pts_time,flags",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        packets = json.loads(result.stdout).get("packets", [])
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return None

    best = None
    for pkt in packets:
        if "K" not in pkt.get("flags", ""):
            continue
        try:
            pts = float(pkt["pts_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if pts <= t + 1e-6 and (best is None or pts > best):
            best = pts
    return best


def report_keyframe_lead_in(input_path: Path, video_index: int, start: float,
                            file_start_time: float, output_path: Path) -> None:
    """Tell the user where a stream-copy cut will really start. ffmpeg's input seek lands
    on the last keyframe at or before the requested time (in pts terms — a keyframe
    displayed a few ms after it counts as after, so the previous GOP is used)."""
    kf = keyframe_at_or_before(input_path, video_index, start + file_start_time)
    if kf is None:
        warn("Couldn't locate the keyframe before the start — the clip may begin earlier "
             "than requested. Use -r/--reencode for an exact start.")
        return

    actual = max(0.0, kf - file_start_time)
    lead_in = start - actual
    if lead_in <= _KEYFRAME_NOTE_THRESHOLD:
        return
    if output_path.suffix.lower() in MP4_EXTS:
        # The MP4 muxer writes an edit list that hides the lead-in, so players start at
        # the exact requested time; the frames are only in the file.
        info(f"Stream-copy keeps {lead_in:.2f}s of lead-in from the previous keyframe "
             f"({format_clock(actual)}); the MP4 edit list hides it, so playback starts "
             "at the requested time")
    else:
        warn(f"Stream-copy can only start on a keyframe: the clip will actually start at "
             f"{format_clock(actual)}, {lead_in:.2f}s before the requested start. "
             "Use -r/--reencode for an exact start.")


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
    parser.add_argument("-f", "--force", action="store_true",
                        help="Replace the output file if it exists (the old one is moved to the Trash)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    require_tool("ffmpeg")
    require_tool("ffprobe")

    if args.end and args.duration:
        die("--end and --duration are mutually exclusive")

    start = parse_time(args.start, "--start") if args.start else 0.0
    end = parse_time(args.end, "--end") if args.end else None
    if args.duration:
        length = parse_time(args.duration, "--duration")
        if length <= 0:
            die("--duration must be greater than zero")
        end = start + length
    if end is not None and end <= start:
        die("--end must be after --start")

    input_path = Path(args.video_file)
    if not input_path.is_file():
        die(f"file not found: {input_path}")

    data = probe(input_path)
    if data is None:
        die(f"ffprobe could not read {input_path}")
    streams = data.get("streams", [])
    video_streams = main_video_streams(streams)
    if not video_streams:
        die(f"no video stream found in {input_path}")

    fmt = data.get("format", {})
    try:
        file_duration = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        file_duration = None
    try:
        file_start_time = float(fmt.get("start_time", 0) or 0)
    except (TypeError, ValueError):
        file_start_time = 0.0

    if file_duration is not None:
        if start >= file_duration:
            die(f"--start {format_clock(start)} is at or past the end of the video "
                f"({format_clock(file_duration)})")
        if end is not None and end >= file_duration:
            warn(f"End {format_clock(end)} is past the end of the video "
                 f"({format_clock(file_duration)}) — cutting to the end instead")
            end = None

    if args.output_file:
        output_path = Path(args.output_file)
    else:
        tag = f"{format_tag(start)}-{format_tag(end) if end is not None else 'end'}"
        output_path = input_path.with_name(f"{input_path.stem}.cut.{tag}{input_path.suffix}")

    check_output_path(input_path, output_path, args.force)

    info(f"Input  : {input_path}")
    info(f"Output : {output_path}")
    info(f"Start  : {format_clock(start)}")
    info(f"End    : {format_clock(end) if end is not None else 'end of file'}")
    info(f"Mode   : {'re-encode' if args.reencode else 'stream-copy (fast)'}")

    if not args.reencode and start > 0:
        report_keyframe_lead_in(input_path, video_streams[0]["index"], start,
                                file_start_time, output_path)

    ffmpeg_args = ["-hide_banner"]
    if start > 0:
        ffmpeg_args += ["-ss", f"{start:.6f}"]
    ffmpeg_args += ["-i", str(input_path)]
    if end is not None:
        # -ss before -i resets output timestamps to 0, so -to would act as a duration.
        # Always pass the true duration with -t instead.
        ffmpeg_args += ["-t", f"{end - start:.6f}"]

    ffmpeg_args += ["-c", "copy"]
    if args.reencode:
        ffmpeg_args += ["-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac"]
    ffmpeg_args += keep_all_streams_args(streams, output_path.suffix.lower())

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
