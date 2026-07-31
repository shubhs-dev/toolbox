"""
concatvid — Concatenate split video parts in a folder using ffmpeg (no re-encoding).

Scans a directory (default: current directory) for video files, groups them by a
common base name with a trailing sequence marker (`Movie 1.mp4`/`Movie 2.mp4`,
`Movie_pt1.mkv`/`Movie_pt2.mkv`, `Movie (01).mp4`/`Movie (02).mp4`, etc.), and
concatenates each group in sequence order via ffmpeg's concat demuxer with
stream-copy (`-c copy`) — no re-encoding. Files with no sequence marker, or whose
marker has no siblings, are left untouched. The output is named after the shared
base name; the original part files are trashed after a successful concat.

Before concatenating, each group is checked via ffprobe for stream-copy compatibility.
Mismatched codecs/audio can't be auto-fixed and are skipped. A resolution-only mismatch
(e.g. a 4K part mixed with 1440p parts) triggers a prompt to downscale the larger parts
to match via `downscalevid` first (skippable/auto-accepted with -y).

Requires:
    ffmpeg, ffprobe, downscalevid (only if a resolution mismatch needs fixing)

Usage:
    concatvid
    concatvid ~/Videos/raw
    concatvid -y ~/Videos/raw     # skip confirmation prompts
    concatvid -n ~/Videos/raw     # preview groups without concatenating
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, die, info, ok, warn
from toolboxcli._common.tooling import require_tool
from toolboxcli._common.trash import move_to_trash

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm", ".wmv"}


@dataclass(frozen=True)
class StreamInfo:
    vcodec: str
    width: int
    height: int
    pix_fmt: str
    acodec: str | None
    sample_rate: str | None
    channels: int | None


def probe(path: Path) -> StreamInfo | None:
    """Read the primary video/audio stream properties of *path* via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,sample_rate,channels",
            "-of", "json",
            str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None

    try:
        streams = json.loads(result.stdout)["streams"]
    except (json.JSONDecodeError, KeyError):
        return None

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or "width" not in video or "height" not in video:
        return None
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    return StreamInfo(
        vcodec=video.get("codec_name", ""),
        width=video["width"],
        height=video["height"],
        pix_fmt=video.get("pix_fmt", ""),
        acodec=audio.get("codec_name") if audio else None,
        sample_rate=audio.get("sample_rate") if audio else None,
        channels=audio.get("channels") if audio else None,
    )


def probe_group(members: list[tuple[int, Path, str]]) -> dict[Path, StreamInfo] | None:
    """Probe every member of a group; return None if any file can't be read."""
    infos: dict[Path, StreamInfo] = {}
    for _num, path, _base in members:
        result = probe(path)
        if result is None:
            return None
        infos[path] = result
    return infos


def find_incompatibilities(infos: dict[Path, StreamInfo]) -> tuple[bool, bool]:
    """Return (resolution_mismatch, other_mismatch) across a group's stream info."""
    values = list(infos.values())
    ref = values[0]
    resolution_mismatch = any((v.width, v.height) != (ref.width, ref.height) for v in values)
    other_mismatch = any(
        (v.vcodec, v.pix_fmt, v.acodec, v.sample_rate, v.channels)
        != (ref.vcodec, ref.pix_fmt, ref.acodec, ref.sample_rate, ref.channels)
        for v in values
    )
    return resolution_mismatch, other_mismatch

_KEYWORD = r"(?:part|pt|cd|disc|disk)"
_SEQUENCE_RE = re.compile(
    rf"^(?P<base>.+?)[\s_.-]*\(?(?:{_KEYWORD}[\s_.-]*)?(?P<num>\d{{1,3}})\)?$",
    re.IGNORECASE,
)


def split_sequence(stem: str) -> tuple[str, int] | None:
    """Split a filename stem into (base_name, sequence_number), or None if no marker."""
    m = _SEQUENCE_RE.match(stem)
    if not m:
        return None
    base = re.sub(r"[\s_.-]+$", "", m.group("base"))
    if not base:
        return None
    return base, int(m.group("num"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concatvid",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("directory", nargs="?", default=".", help="Directory to scan (default: .)")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview groups without concatenating")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.dry_run:
        require_tool("ffmpeg")
        require_tool("ffprobe")

    target_dir = Path(args.directory)
    if not target_dir.is_dir():
        die(f"not a directory: {target_dir}")

    videos = sorted(p for p in target_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)

    groups: dict[tuple[str, str], list[tuple[int, Path, str]]] = {}
    for video in videos:
        split = split_sequence(video.stem)
        if split is None:
            continue
        base, num = split
        key = (base.lower(), video.suffix.lower())
        groups.setdefault(key, []).append((num, video, base))

    groups = {k: v for k, v in groups.items() if len(v) > 1}

    if not groups:
        ok("No groups of split video parts found.")
        return

    info(f"Found {len(groups)} group(s) of split video parts:")
    for (base_lower, ext), members in sorted(groups.items()):
        members.sort(key=lambda m: (m[0], m[1].name))
        console.print()
        console.print(f"[bold]{base_lower}{ext}[/bold]  ({len(members)} parts)")
        nums = [m[0] for m in members]
        if len(set(nums)) != len(nums):
            warn("  Duplicate sequence numbers detected — ordering may be wrong")
        for num, path, _base in members:
            console.print(f"  [{num}] {path.name}")

    if args.dry_run:
        console.print()
        info(f"Dry run complete. {len(groups)} group(s) would be concatenated.")
        return

    concatenated = 0
    trashed = 0

    for (base_lower, ext), members in sorted(groups.items()):
        display_base = members[0][2]
        console.print()

        scaled_paths: dict[Path, Path] = {}

        infos = probe_group(members)
        if infos is None:
            warn(f"ffprobe couldn't read one or more parts of '{display_base}{ext}' — proceeding without a compatibility check")
        else:
            resolution_mismatch, other_mismatch = find_incompatibilities(infos)

            if other_mismatch:
                warn(f"Parts of '{display_base}{ext}' have mismatched codec/format — can't stream-copy concat them:")
                for _num, path, _base in members:
                    si = infos[path]
                    console.print(
                        f"  {path.name}: {si.width}x{si.height} {si.vcodec}/{si.pix_fmt}, "
                        f"audio {si.acodec or 'none'}"
                    )
                warn("Skipping — re-encode the parts to a matching format first.")
                continue

            if resolution_mismatch:
                target = min(infos.values(), key=lambda si: si.width * si.height)
                resolutions = sorted({(si.width, si.height) for si in infos.values()}, reverse=True)
                res_str = ", ".join(f"{w}x{h}" for w, h in resolutions)
                do_scale = True
                if not args.yes:
                    do_scale = confirm(
                        f"Parts of '{display_base}{ext}' have mismatched resolutions ({res_str}). "
                        f"Downscale larger parts to {target.width}x{target.height} before concatenating?",
                        default="y",
                    ) == "y"
                if not do_scale:
                    info("Skipped.")
                    continue

                require_tool("downscalevid")
                scale_failed = False
                for _num, path, _base in members:
                    si = infos[path]
                    if (si.width, si.height) == (target.width, target.height):
                        continue
                    tmp_fd, tmp_name = tempfile.mkstemp(suffix=path.suffix, dir=target_dir)
                    os.close(tmp_fd)
                    tmp_path = Path(tmp_name)
                    tmp_path.unlink()
                    result = subprocess.run(
                        ["downscalevid", str(path), str(tmp_path), "-r", f"{target.width}x{target.height}"]
                    )
                    if result.returncode != 0 or not tmp_path.exists():
                        warn(f"downscalevid failed for '{path.name}', skipping group")
                        scale_failed = True
                        break
                    scaled_paths[path] = tmp_path

                if scale_failed:
                    for tmp in scaled_paths.values():
                        tmp.unlink(missing_ok=True)
                    continue

        if not args.yes:
            reply = confirm(f"Concatenate {len(members)} parts into '{display_base}{ext}'?", default="y")
            if reply != "y":
                info("Skipped.")
                for tmp in scaled_paths.values():
                    tmp.unlink(missing_ok=True)
                continue

        output_path = target_dir / f"{display_base}{ext}"
        if output_path.exists():
            output_path = target_dir / f"{display_base}.concat{ext}"
            warn(f"Output name already exists, using '{output_path.name}' instead")

        list_fd, list_name = tempfile.mkstemp(suffix=".txt", text=True)
        list_path = Path(list_name)
        try:
            with os.fdopen(list_fd, "w", encoding="utf-8") as f:
                for _num, path, _base in members:
                    concat_path = scaled_paths.get(path, path)
                    escaped = str(concat_path.resolve()).replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(list_path),
                    "-c", "copy",
                    str(output_path),
                ]
            )
        finally:
            list_path.unlink(missing_ok=True)
            for tmp in scaled_paths.values():
                tmp.unlink(missing_ok=True)

        if result.returncode != 0:
            if output_path.exists():
                output_path.unlink()
            warn(f"ffmpeg failed for '{display_base}{ext}' (exit code {result.returncode}), skipping")
            continue

        for _num, path, _base in members:
            move_to_trash(path)
            trashed += 1

        ok(f"Concatenated → {output_path}")
        concatenated += 1

    console.print()
    ok(f"Done. {concatenated} group(s) concatenated, {trashed} file(s) trashed.")


if __name__ == "__main__":
    main()
