"""
concatvid — Concatenate split video parts in a folder using ffmpeg.

Scans a directory (default: current directory) for video files, groups them by a
common base name with a trailing sequence marker (`Movie 1.mp4`/`Movie 2.mp4`,
`Movie_pt1.mkv`/`Movie_pt2.mkv`, `Movie (01).mp4`/`Movie (02).mp4`, etc.), and
concatenates each group in sequence order via ffmpeg's concat demuxer with
stream-copy (`-c copy`) — no re-encoding. Files with no sequence marker, or whose
marker has no siblings, are left untouched. The output is named after the shared
base name; the original part files are trashed after a successful concat.

Before concatenating, each group is checked via ffprobe for stream-copy compatibility.
Parts that disagree on resolution, video codec, pixel format or audio layout are
normalized to the group's dominant format first (prompted, auto-accepted with -y):
the smallest resolution wins, so parts are only ever downscaled, and parts already
in the target format are left untouched. A part whose video already matches has only
its audio re-encoded.

Re-encoding runs on the GPU when a supported one is available — NVIDIA (NVENC), AMD
(AMF) or Apple silicon (VideoToolbox) — and falls back to the CPU encoder otherwise.
Override with -g/--gpu or the CONCATVID_GPU env var.

Requires:
    ffmpeg, ffprobe

Usage:
    concatvid
    concatvid ~/Videos/raw
    concatvid -y ~/Videos/raw     # skip confirmation prompts
    concatvid -n ~/Videos/raw     # preview groups without concatenating
    concatvid -g cpu ~/Videos/raw # force software encoding when re-encoding
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, die, info, ok, warn
from toolboxcli._common.encoders import (
    GPU_CHOICES,
    Encoder,
    cpu_encoder,
    gpu_preference,
    select_encoder,
)
from toolboxcli._common.tooling import require_tool
from toolboxcli._common.trash import move_to_trash
from toolboxcli.concatvid.core import (
    VIDEO_EXTS,
    StreamInfo,
    TargetProfile,
    audio_presence_mismatch,
    choose_target,
    find_incompatibilities,
    needs_normalizing,
    normalize_command,
    parse_streams,
    split_sequence,
    video_matches,
)

GPU_ENV_VAR = "CONCATVID_GPU"


def probe(path: Path) -> StreamInfo | None:
    """Read the stream properties of *path* via ffprobe."""
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
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    return parse_streams(payload)


def probe_group(members: list[tuple[int, Path, str]]) -> dict[Path, StreamInfo] | None:
    """Probe every member of a group; return None if any file can't be read."""
    infos: dict[Path, StreamInfo] = {}
    for _num, path, _base in members:
        result = probe(path)
        if result is None:
            return None
        infos[path] = result
    return infos


def normalize_part(
    src: Path,
    dst: Path,
    stream_info: StreamInfo,
    target: TargetProfile,
    encoder: Encoder | None,
    preference: str,
) -> bool:
    """Rewrite one part into the target format, retrying on CPU if an auto GPU fails."""
    result = subprocess.run(normalize_command(src, dst, stream_info, target, encoder))

    # A GPU we picked ourselves can still fail on a specific input (unsupported
    # pixel format, session limit, driver hiccup) — retry once on the CPU rather
    # than making the user rerun with -g cpu. An explicitly requested GPU is not
    # second-guessed.
    if result.returncode != 0 and encoder is not None and encoder.is_hardware and preference == "auto":
        dst.unlink(missing_ok=True)
        warn(f"{encoder.label} encode failed (exit {result.returncode}), retrying on CPU...")
        fallback = cpu_encoder(target.codec)
        result = subprocess.run(normalize_command(src, dst, stream_info, target, fallback))

    if result.returncode != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concatvid",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("directory", nargs="?", default=".", help="Directory to scan (default: .)")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview groups without concatenating")
    parser.add_argument(
        "-g", "--gpu", choices=GPU_CHOICES, default=None,
        help="Encoder to use when parts need re-encoding (default: auto — first "
             f"available GPU, else CPU). Env: {GPU_ENV_VAR}",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.dry_run:
        require_tool("ffmpeg")
        require_tool("ffprobe")

    preference = args.gpu or gpu_preference(GPU_ENV_VAR)

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
    reencoded = 0

    for (base_lower, ext), members in sorted(groups.items()):
        display_base = members[0][2]
        console.print()

        normalized: dict[Path, Path] = {}

        infos = probe_group(members)
        if infos is None:
            warn(f"ffprobe couldn't read one or more parts of '{display_base}{ext}' — proceeding without a compatibility check")
        else:
            ordered = [infos[path] for _num, path, _base in members]
            resolution_mismatch, mismatched = find_incompatibilities(ordered)

            if resolution_mismatch or mismatched:
                reasons = (["resolution"] if resolution_mismatch else []) + mismatched
                warn(
                    f"Parts of '{display_base}{ext}' differ in {', '.join(reasons)} "
                    f"— can't stream-copy concat them as-is:"
                )
                for _num, path, _base in members:
                    console.print(f"  {path.name}: {infos[path].describe()}")

                if audio_presence_mismatch(ordered):
                    warn("Skipping — some parts have no audio track at all, which "
                         "re-encoding can't reconcile. Fix those parts first.")
                    continue

                target = choose_target(ordered)
                drop_extras = any(p.has_extra_streams for p in ordered)
                todo = [
                    (path, infos[path])
                    for _num, path, _base in members
                    if needs_normalizing(infos[path], target, drop_extras)
                ]

                console.print(f"  → target: {target.describe()}")
                if drop_extras:
                    warn("  Extra streams (subtitles, secondary audio) will be dropped so "
                         "every part ends up with the same stream layout.")

                do_encode = True
                if not args.yes:
                    do_encode = confirm(
                        f"Re-encode {len(todo)} of {len(members)} part(s) to match before concatenating?",
                        default="y",
                    ) == "y"
                if not do_encode:
                    info("Skipped.")
                    continue

                # Only probe for a GPU if some part actually needs its video redone —
                # an audio-only fixup stream-copies the video and needs no encoder.
                encoder: Encoder | None = None
                if any(not video_matches(si, target) for _path, si in todo):
                    encoder = select_encoder(target.codec, preference)
                    info(f"Encoder: {encoder.label} ({encoder.name})")

                failed = False
                for path, stream_info in todo:
                    tmp_fd, tmp_name = tempfile.mkstemp(suffix=path.suffix, dir=target_dir)
                    os.close(tmp_fd)
                    tmp_path = Path(tmp_name)
                    tmp_path.unlink()
                    if not normalize_part(path, tmp_path, stream_info, target, encoder, preference):
                        warn(f"re-encode failed for '{path.name}', skipping group")
                        failed = True
                        break
                    normalized[path] = tmp_path
                    reencoded += 1

                if failed:
                    for tmp in normalized.values():
                        tmp.unlink(missing_ok=True)
                    continue

        if not args.yes:
            reply = confirm(f"Concatenate {len(members)} parts into '{display_base}{ext}'?", default="y")
            if reply != "y":
                info("Skipped.")
                for tmp in normalized.values():
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
                    concat_path = normalized.get(path, path)
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
            for tmp in normalized.values():
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
    ok(
        f"Done. {concatenated} group(s) concatenated, {reencoded} part(s) re-encoded, "
        f"{trashed} file(s) trashed."
    )


if __name__ == "__main__":
    main()
