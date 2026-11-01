"""
concatvid — Join videos end to end using ffmpeg, re-encoding only what has to be.

Give it the videos to join, in order, and optionally a name for the result:

    concatvid "Day 1.mp4" "Day 2.mp4" "Day 3.mp4" -o "Holiday"

A bare -o name lands next to the first part and gets its extension; a name with a folder
in it is used as given. Without -o the result is named after the parts' shared base name
(`Movie 1.mp4` + `Movie 2.mp4` → `Movie.mp4`), never over an existing file. An existing
-o file is only replaced with -f/--force (the old one goes to the Trash).

Or let it find the parts itself: -a/--auto scans a folder (default: the current one) for
videos sharing a base name with a trailing sequence marker (`Movie 1.mp4`/`Movie 2.mp4`,
`Movie_pt1.mkv`/`Movie_pt2.mkv`, `Movie (01).mp4`/`Movie (02).mp4`, etc.) and joins each
group in sequence order. Files with no marker, or whose marker has no siblings, are left
untouched; outputs are named `<base>`, or `<base>.concat`, `<base>.concat2`, … if taken.

Either way, parts are joined with ffmpeg's concat demuxer and stream-copied (`-c copy`),
keeping every video, audio and subtitle track. Before joining, the parts are checked via
ffprobe for stream-copy compatibility. Parts that disagree on resolution, video codec,
pixel format, audio layout or the format of any other track are normalized to the
dominant format first (prompted, auto-accepted with -y): the smallest resolution wins,
so parts are only ever downscaled, parts already in the target format are left
untouched, and a part whose video already matches has only its audio re-encoded. The
original parts are trashed once the result's duration is confirmed to cover all of them
(otherwise they're left in place with a warning).

Re-encoding runs on the GPU when a supported one is available — NVIDIA (NVENC), AMD
(AMF) or Apple silicon (VideoToolbox) — and falls back to the CPU encoder otherwise.
Override with -g/--gpu or the CONCATVID_GPU env var.

Requires:
    ffmpeg, ffprobe

Usage:
    concatvid a.mp4 b.mp4 c.mp4                  # join in this order → a.concat.mp4
    concatvid "Day 1.mp4" "Day 2.mp4" -o Holiday # → Holiday.mp4, next to Day 1.mp4
    concatvid a.mp4 b.mp4 -o ~/out/joined.mkv    # explicit path and container
    concatvid a.mp4 b.mp4 -o Holiday -f          # replace an existing Holiday.mp4
    concatvid -a                                 # scan the current folder for parts
    concatvid -a ~/Videos/raw -y                 # scan a folder, no prompts
    concatvid -a ~/Videos/raw -n                 # preview groups without joining
    concatvid -g cpu a.mp4 b.mp4                 # force software encoding when re-encoding
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from dataclasses import dataclass
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
from toolboxcli._common.ffprobe import probe as probe_raw
from toolboxcli._common.output import atomic_output, is_same_file
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
    """Read the stream properties (and duration) of *path* via ffprobe."""
    payload = probe_raw(path)
    return parse_streams(payload) if payload is not None else None


def probe_group(parts: list[Path]) -> dict[Path, StreamInfo] | None:
    """Probe every part; return None if any file can't be read."""
    infos: dict[Path, StreamInfo] = {}
    for path in parts:
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

    # mkstemp already created dst, so existence alone proves nothing.
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        return False
    return True


class _ConcatFailed(Exception):
    """Raised inside atomic_output() so a failed concat discards the temp output."""

    def __init__(self, returncode: int) -> None:
        super().__init__(returncode)
        self.returncode = returncode


# Slack per part when checking the output covers every part: a part's container duration
# and what the concat demuxer actually reads from it can differ by a few frames.
DURATION_SLACK_PER_PART = 0.5


def media_duration(path: Path) -> float | None:
    """Container duration in seconds, or None if ffprobe can't tell."""
    data = probe_raw(path)
    try:
        return float(data["format"]["duration"]) if data else None
    except (KeyError, TypeError, ValueError):
        return None


def free_output_path(target_dir: Path, base: str, ext: str) -> Path:
    """`<base><ext>`, or the first of `<base>.concat<ext>`, `<base>.concat2<ext>`, … that
    doesn't exist yet. Picking a free name up front is what lets the concat never
    overwrite anything — ffmpeg's `-y` used to clobber a leftover `.concat` file."""
    candidate = target_dir / f"{base}{ext}"
    n = 1
    while candidate.exists():
        candidate = target_dir / f"{base}.concat{n if n > 1 else ''}{ext}"
        n += 1
    return candidate


def temp_part_path(target_dir: Path, src: Path) -> Path:
    """A fresh hidden path for a normalized copy of *src*, named so a leftover from a
    crash is recognisably ours. mkstemp reserves the name; ffmpeg then writes to it."""
    fd, name = tempfile.mkstemp(prefix=f".concatvid-{src.stem}.", suffix=src.suffix, dir=target_dir)
    os.close(fd)
    return Path(name)


@dataclass
class Stats:
    concatenated: int = 0
    reencoded: int = 0
    trashed: int = 0
    unverified: int = 0


def concat_group(
    parts: list[Path],
    label: str,
    output_path: Path,
    overwrite: bool,
    args: argparse.Namespace,
    preference: str,
    stats: Stats,
) -> None:
    """Concatenate *parts*, in order, into *output_path*: check compatibility, normalize
    mismatched parts, stream-copy concat, verify the result, then trash the parts.
    Shared by both modes — the automatic folder scan and an explicit file list."""
    work_dir = output_path.parent
    normalized: dict[Path, Path] = {}
    temps: list[Path] = []  # every normalized-part temp file, cleaned up however this ends
    try:
        infos = probe_group(parts)
        if infos is None:
            warn(f"ffprobe couldn't read one or more parts of '{label}' — proceeding without a compatibility check")
        else:
            ordered = [infos[path] for path in parts]
            resolution_mismatch, mismatched = find_incompatibilities(ordered)

            if resolution_mismatch or mismatched:
                reasons = (["resolution"] if resolution_mismatch else []) + mismatched
                warn(
                    f"Parts of '{label}' differ in {', '.join(reasons)} "
                    f"— can't stream-copy concat them as-is:"
                )
                for path in parts:
                    console.print(f"  {path.name}: {infos[path].describe()}")

                if audio_presence_mismatch(ordered):
                    warn("Skipping — some parts have no audio track at all, which "
                         "re-encoding can't reconcile. Fix those parts first.")
                    return

                target = choose_target(ordered)
                drop_extras = any(p.has_extra_streams for p in ordered)
                todo = [
                    (path, infos[path]) for path in parts
                    if needs_normalizing(infos[path], target, drop_extras)
                ]

                console.print(f"  → target: {target.describe()}")
                if drop_extras:
                    warn("  Extra streams (subtitles, secondary audio) will be dropped so "
                         "every part ends up with the same stream layout.")

                if not args.yes and confirm(
                    f"Re-encode {len(todo)} of {len(parts)} part(s) to match before concatenating?",
                    default="y",
                ) != "y":
                    info("Skipped.")
                    return

                # Only probe for a GPU if some part actually needs its video redone —
                # an audio-only fixup stream-copies the video and needs no encoder.
                encoder: Encoder | None = None
                if any(not video_matches(si, target) for _path, si in todo):
                    encoder = select_encoder(target.codec, preference)
                    info(f"Encoder: {encoder.label} ({encoder.name})")

                for path, stream_info in todo:
                    tmp_path = temp_part_path(work_dir, path)
                    temps.append(tmp_path)
                    if not normalize_part(path, tmp_path, stream_info, target, encoder, preference):
                        warn(f"re-encode failed for '{path.name}', skipping '{label}'")
                        return
                    normalized[path] = tmp_path
                    stats.reencoded += 1

        if not args.yes and confirm(
            f"Concatenate {len(parts)} parts into '{output_path.name}'?", default="y",
        ) != "y":
            info("Skipped.")
            return

        list_fd, list_name = tempfile.mkstemp(suffix=".txt", text=True)
        list_path = Path(list_name)
        try:
            with os.fdopen(list_fd, "w", encoding="utf-8") as f:
                for path in parts:
                    escaped = str(normalized.get(path, path).resolve()).replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            # Without explicit maps ffmpeg keeps one track of each type, silently
            # dropping second-language audio and extra subtitles. `V` (not `v`) skips
            # cover art. -y only ever overwrites atomic_output()'s own temp file.
            with atomic_output(output_path, force=overwrite) as tmp_out:
                result = subprocess.run(
                    [
                        "ffmpeg", "-y", "-nostdin",
                        "-f", "concat", "-safe", "0",
                        "-i", str(list_path),
                        "-map", "0:V", "-map", "0:a?", "-map", "0:s?",
                        "-c", "copy",
                        str(tmp_out),
                    ]
                )
                if result.returncode != 0:
                    raise _ConcatFailed(result.returncode)
        except _ConcatFailed as e:
            warn(f"ffmpeg failed for '{label}' (exit code {e.returncode}), skipping")
            return
        finally:
            list_path.unlink(missing_ok=True)

        # ffmpeg's concat demuxer exits 0 even when it couldn't open a part — the
        # output just stops early. Only trash the parts once the output provably
        # covers all of them.
        part_durations = [
            media_duration(normalized[path]) if path in normalized
            else infos[path].duration if infos is not None
            else media_duration(path)
            for path in parts
        ]
        actual = media_duration(output_path)
        if None in part_durations or actual is None:
            warn(f"Couldn't verify '{output_path.name}' contains every part — "
                 "left the parts in place, check it before deleting them")
            stats.unverified += 1
            return
        expected = sum(part_durations)
        if actual + DURATION_SLACK_PER_PART * len(parts) < expected:
            warn(f"'{output_path.name}' is {actual:.1f}s but the parts add up to "
                 f"{expected:.1f}s — some part didn't make it in. Left the parts in place.")
            return

        for path in parts:
            move_to_trash(path)
            stats.trashed += 1

        ok(f"Concatenated → {output_path}")
        stats.concatenated += 1
    finally:
        for tmp in temps:
            tmp.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concatvid",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="*", metavar="VIDEO",
                        help="Videos to join, in the order given")
    parser.add_argument("-o", "--output", metavar="NAME", default=None,
                        help="Name of the joined video (default: the parts' shared base name). "
                             "A bare name goes next to the first part; no extension → the first part's")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Replace an existing -o/--output file (the old one goes to the Trash)")
    parser.add_argument("-a", "--auto", nargs="?", const=".", default=None, metavar="DIR",
                        help="Instead of a file list, scan DIR (default: .) for numbered parts "
                             "and join every group found")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Show what would be joined, and into what, without doing it")
    parser.add_argument(
        "-g", "--gpu", choices=GPU_CHOICES, default=None,
        help="Encoder to use when parts need re-encoding (default: auto — first "
             f"available GPU, else CPU). Env: {GPU_ENV_VAR}",
    )
    return parser


def named_output_path(first: Path, name: str) -> Path:
    """Where `-o NAME` puts the joined video. A bare name lands next to the first part
    (that's where the parts are, and usually where you'd look); anything with a folder in
    it is used as given. With no video extension, the first part's is added — checked
    against VIDEO_EXTS rather than Path.suffix, so `Trip v1.2` doesn't read as a `.2` file."""
    raw = Path(name).expanduser()
    if raw.suffix.lower() not in VIDEO_EXTS:
        raw = raw.with_name(raw.name + first.suffix)
    has_folder = raw.parent != Path(".") or name.startswith(("./", ".\\"))
    return raw if has_folder else first.parent / raw


def default_output_path(first: Path) -> Path:
    """The parts' shared base name (`Movie 1.mp4` → `Movie.mp4`), or `<first>.concat` when the
    first part has no sequence marker — never an existing file."""
    split = split_sequence(first.stem)
    base = split[0] if split else first.stem
    return free_output_path(first.parent, base, first.suffix)


def run_manual(parser: argparse.ArgumentParser, args: argparse.Namespace,
               preference: str) -> None:
    parts = [Path(f) for f in args.files]
    if len(parts) == 1 and parts[0].is_dir():
        die(f"'{parts[0]}' is a folder — use -a {parts[0]} to scan it for numbered parts")
    if len(parts) < 2:
        parser.error("give at least two videos to join (or -a to scan a folder)")
    for p in parts:
        if not p.is_file():
            die(f"not a file: {p}")
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            if is_same_file(a, b):
                die(f"'{a}' and '{b}' are the same file — each part can only be given once")

    if args.output:
        output_path = named_output_path(parts[0], args.output)
        for p in parts:
            if is_same_file(p, output_path):
                die(f"the output can't be one of the parts: {output_path}")
        if not output_path.parent.is_dir():
            die(f"output folder does not exist: {output_path.parent}")
        if output_path.exists() and not args.force:
            die(f"{output_path} already exists — use -f/--force to replace it "
                "(the old file is moved to the Trash)")
    else:
        output_path = default_output_path(parts[0])

    info(f"Joining {len(parts)} parts, in this order:")
    for n, p in enumerate(parts, 1):
        console.print(f"  [{n}] {p}")
    console.print(f"  → {output_path}")

    if args.dry_run:
        console.print()
        info("Dry run — nothing joined.")
        return

    console.print()
    stats = Stats()
    concat_group(parts, output_path.name, output_path, bool(args.output and args.force),
                 args, preference, stats)
    report(stats, single=True)


def run_auto(args: argparse.Namespace, preference: str) -> None:
    target_dir = Path(args.auto).expanduser()
    if not target_dir.is_dir():
        die(f"not a directory: {target_dir}")

    # Dotfiles are skipped: macOS `._Movie 1.mp4` AppleDouble files on external drives
    # aren't videos, and a crash-leftover `.concatvid-*` temp part must never be re-read
    # as a part.
    videos = sorted(
        p for p in target_dir.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in VIDEO_EXTS
    )

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

    stats = Stats()
    for (_base_lower, ext), members in sorted(groups.items()):
        display_base = members[0][2]
        console.print()
        output_path = free_output_path(target_dir, display_base, ext)
        if output_path.name != f"{display_base}{ext}":
            warn(f"Output name already exists, using '{output_path.name}' instead")
        concat_group([path for _num, path, _base in members], f"{display_base}{ext}",
                     output_path, False, args, preference, stats)
    report(stats, single=False)


def report(stats: Stats, single: bool) -> None:
    console.print()
    if single:
        if stats.concatenated:
            ok(f"Done. {stats.reencoded} part(s) re-encoded, {stats.trashed} file(s) trashed.")
        elif stats.unverified:
            warn("The joined video was written but couldn't be verified — the parts were kept.")
        return
    ok(
        f"Done. {stats.concatenated} group(s) concatenated, {stats.reencoded} part(s) re-encoded, "
        f"{stats.trashed} file(s) trashed."
    )
    if stats.unverified:
        warn(f"{stats.unverified} more group(s) were written but couldn't be verified — "
             "their parts were kept.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.auto is not None and args.files:
        parser.error("give either a list of videos or -a/--auto, not both")
    if args.auto is not None and args.output:
        parser.error("-o/--output names a single joined video, so it only works with a "
                     "list of videos, not -a/--auto")
    if args.auto is None and not args.files:
        parser.error("give the videos to join, in order (or -a to scan a folder for numbered parts)")

    if not args.dry_run:
        require_tool("ffmpeg")
        require_tool("ffprobe")

    preference = args.gpu or gpu_preference(GPU_ENV_VAR)

    if args.auto is not None:
        run_auto(args, preference)
    else:
        run_manual(parser, args, preference)


if __name__ == "__main__":
    main()
