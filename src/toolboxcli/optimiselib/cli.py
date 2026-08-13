"""
optimiselib — Watch a library root, optimise new videos to 1080p, tag and sort them.

Runs continuously on a media server. For every video that appears directly in the library
root it will:

  1. Transcode it with HandBrake — the 720p preset for <=720p sources, the 1080p preset
     otherwise. Neither preset upscales. Every audio track is kept, encoded to stereo AAC
     at 160k. The result is kept only when it is smaller than the original; the original
     goes to the Recycle Bin, never deleted outright.
  2. Rewrite the filename to carry a resolution tag, so 720p files you may want to upscale
     later with a better tool are visible at a glance:
         People - Title - Trip  ->  People - Title - Trip [1080p].mp4
     Tags you added yourself are preserved: [Restored 720p] -> [Restored 1080p].
  3. Compare it against any copy already in the library. A higher-resolution or more
     complete version replaces the old one (which goes to the Recycle Bin); anything
     ambiguous is parked in _review/ for you to decide.
  4. Move it into the folder named by its Trip field — the last " - " separated part of
     the name.

Only files sitting directly in the root are touched. Subfolders — including the trip
folders and any folder you use to stage files by hand — are left completely alone.

Requires:
    HandBrakeCLI, ffprobe

Usage:
    optimiselib /srv/library              # watch, poll every 30s
    optimiselib -O                        # process what's there, then exit
    optimiselib -n                        # dry run: show what would happen
    optimiselib -r                        # report sub-1080p files and the review queue
    optimiselib -H 01:00-07:00 -L 4       # only encode off-hours, and back off under load
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich import box

from toolboxcli._common.console import console, die, info, ok, warn
from toolboxcli._common.handbrake import (
    available_encoders,
    load_preset,
    read_preset_settings,
    transcode,
    wait_stable,
)
from toolboxcli._common.humanize import human_size as fmt_size
from toolboxcli._common.progress import bar as progress_bar
from toolboxcli._common.tooling import require_tool
from toolboxcli._common.trash import move_to_trash

from toolboxcli.optimiselib import core

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".vob", ".3gp", ".mts",
}

LOG_NAME = ".optimiselib.json"
REVIEW_DIR = "_review"

_log_lock = threading.Lock()


# ── State log ─────────────────────────────────────────────────────────

def load_log(root: Path) -> dict:
    p = root / LOG_NAME
    if p.exists():
        try:
            with p.open() as f:
                return json.load(f)
        except json.JSONDecodeError:
            warn(f"Unreadable log at {p}, starting fresh")
    return {}


def save_log(root: Path, log: dict) -> None:
    with (root / LOG_NAME).open("w") as f:
        json.dump(log, f, indent=2)


def update_log(root: Path, log: dict, key: str, value: dict) -> None:
    with _log_lock:
        log[key] = value
        save_log(root, log)


# ── Probing ───────────────────────────────────────────────────────────

def probe(path: Path) -> tuple[int, float, int] | None:
    """Return (height, duration_seconds, bit_depth), or None if ffprobe can't read it."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_streams", "-show_format",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    video = next(
        (s for s in payload.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if video is None or "height" not in video:
        return None

    try:
        duration = float(payload.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0

    depth = core.bit_depth(video.get("pix_fmt", ""), video.get("bits_per_raw_sample"))
    return int(video["height"]), duration, depth


# ── Filesystem helpers ────────────────────────────────────────────────

def scan(root: Path):
    """Video files directly in *root* — never subfolders, so trip folders and any
    staging folder you keep by hand are left alone."""
    for f in sorted(root.iterdir()):
        if f.name.startswith(".") or not f.is_file():
            continue
        if f.suffix.lower() in (".tmp", ".processing"):
            continue
        if f.suffix.lower() not in VIDEO_EXTS:
            continue
        yield f


def find_folder(base: Path, name: str) -> Path | None:
    """Case-insensitive recursive search for a folder called *name*."""
    name_lower = name.lower()
    for dirpath, dirnames, _files in os.walk(base):
        if REVIEW_DIR in dirnames:
            dirnames.remove(REVIEW_DIR)
        for d in dirnames:
            if d.lower() == name_lower:
                return Path(dirpath) / d
    return None


def find_existing_copy(folder: Path, identity: str, exclude: Path | None = None) -> Path | None:
    """The file in *folder* that is the same video as *identity*, ignoring tag groups."""
    if not folder.is_dir():
        return None
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
            continue
        if exclude is not None and f == exclude:
            continue
        if core.parse_stem(f.stem).identity() == identity:
            return f
    return None


def recover_interrupted(root: Path) -> list[str]:
    """Undo a half-finished run: restore *.processing sources, drop *.tmp outputs.

    A daemon that gets killed mid-encode must come back without losing a source file.
    """
    recovered = []
    for f in sorted(root.iterdir()):
        if f.suffix.lower() == ".processing" and f.is_file():
            original = f.parent / f.stem
            try:
                f.rename(original)
                recovered.append(original.name)
            except OSError as exc:
                warn(f"Could not restore {f.name}: {exc}")
    for tmp in sorted(root.glob("*.tmp")):
        try:
            tmp.unlink()
        except OSError:
            pass
    if recovered:
        warn(f"Recovered {len(recovered)} interrupted file(s): {', '.join(recovered)}")
    return recovered


def unique_path(path: Path) -> Path:
    """A non-colliding variant of *path*, so parking a file never overwrites one."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem} ({int(time.time())}){path.suffix}")


# ── Throttles ─────────────────────────────────────────────────────────

def load_ok(max_load: float | None) -> bool:
    """False when the machine is too busy to start another encode.

    This is the throttle that actually protects a live Jellyfin stream, because it reacts
    to real contention rather than guessing at a quiet hour.
    """
    if max_load is None:
        return True
    try:
        return os.getloadavg()[0] <= max_load
    except (OSError, AttributeError):   # not available on Windows
        return True


# ── Encoder setup ─────────────────────────────────────────────────────

class EncoderPlan:
    """Which backend to use, and the HandBrake args that select it per preset."""

    def __init__(self, backend, auto_selected: bool, quality_override, presets: dict,
                 available: set, bit_depth_pref: str):
        self.backend = backend
        self.auto_selected = auto_selected
        self.quality_override = quality_override
        self._presets = presets   # preset stem -> (encoder, quality)
        self.available = available
        self.bit_depth_pref = bit_depth_pref

    def wants_10bit(self, source_depth: int) -> bool:
        return core.want_10bit(self.bit_depth_pref, source_depth)

    def has_10bit(self, backend=None) -> bool:
        backend = backend or self.backend
        return bool(backend.encoder_10bit) and backend.encoder_10bit in self.available

    def args_for(self, preset_stem: str, force_cpu: bool = False,
                 ten_bit: bool = False) -> list[str]:
        encoder, cq = self._presets[preset_stem]
        backend = core.BACKENDS["cpu"] if force_cpu else self.backend
        return core.encoder_args(
            backend, encoder, cq, self.quality_override,
            ten_bit=ten_bit, available=self.available,
        )


def build_encoder_plan(args, preset_stems: list[str]) -> EncoderPlan:
    presets = {}
    for stem in preset_stems:
        settings = read_preset_settings(stem)
        presets[stem] = (
            settings.get("VideoEncoder", ""),
            float(settings.get("VideoQualitySlider", 22)),
        )

    # Every preset is authored against the same encoder; the first is representative.
    preset_encoder = presets[preset_stems[0]][0]

    preference = args.gpu or os.environ.get("OPTIMISELIB_GPU", "auto")
    if preference not in core.GPU_CHOICES:
        die(f"Unknown --gpu value: {preference} (choose from {', '.join(core.GPU_CHOICES)})")

    available = available_encoders()
    if not available:
        warn("Could not read HandBrake's encoder list — assuming the preset's own encoder works")
        available = {preset_encoder}

    backend, auto_selected = core.select_backend(
        preference, available, preset_encoder,
    )
    if backend is None:
        die(
            f"No usable encoder: '{preference}' is not available in this HandBrake build. "
            f"Available: {', '.join(sorted(available)) or 'none'}"
        )

    return EncoderPlan(backend, auto_selected, args.quality, presets,
                       available, args.bit_depth)


# ── Per-file pipeline ─────────────────────────────────────────────────

MAX_FAILURES = 3


def should_process(video: Path, log: dict) -> bool:
    """Whether a file in the root is worth another pass.

    Everything in the root is a candidate — a file the log calls "sorted" that is still
    here means you moved it back to be re-filed. Only two things are skipped, both to stop
    a long-running watcher spinning on the same file every interval.
    """
    entry = log.get(video.name)
    if entry is None:
        return True

    status = entry.get("status")
    if status == "awaiting_trip" and not core.parse_stem(video.stem).trip:
        return False   # nothing changed since we last warned about it
    if status == "failed" and entry.get("failures", 1) >= MAX_FAILURES:
        return False   # give up rather than retry a broken file forever
    if status == "skipped" and entry.get("reason") == "destination exists":
        # Stays in the root until you move or rename something, and re-encoding it on
        # every poll in the meantime would burn the GPU indefinitely. -F retries it.
        return False
    return True


def seen_identities(log: dict) -> set[str]:
    """Every video the log has already optimised, keyed by tag-stripped name."""
    return {core.parse_stem(Path(name).stem).identity() for name in log}


def already_optimised(video: Path, height: int, depth: int, seen: set[str]) -> bool:
    """True when this file has been through the pipeline before and needn't be re-encoded.

    This is what makes the round trip cheap when you stage a file in a folder by hand and
    later move it back to the root to be sorted: the name already carries the tags the
    encode would produce, and the log has seen this video, so only the sort step is left.
    """
    parsed = core.parse_stem(video.stem)
    tagged = any(core.RESOLUTION_TOKEN_RE.match(t) for t in parsed.tags)
    matches = core.apply_tags(video.stem, height, depth) == video.stem
    return tagged and matches and parsed.identity() in seen


def plan_destination(root: Path, stem: str) -> tuple[str, Path | None]:
    """Return (trip, existing_folder_or_None) for a tagged stem."""
    trip = core.parse_stem(stem).trip
    if not trip:
        return "", None
    return trip, find_folder(root, trip)


def resolve_duplicate(root: Path, video: Path, height: int, duration: float,
                      dest_folder: Path | None, log: dict, policy: str):
    """Compare *video* with any copy already filed under *dest_folder*.

    Returns (verdict, existing_path) — verdict is None when there's nothing to compare.
    """
    if dest_folder is None:
        return None, None

    identity = core.parse_stem(video.stem).identity()
    existing = find_existing_copy(dest_folder, identity, exclude=video)
    if existing is None:
        return None, None

    # A 1440p and a 1080p source both come out of hw-1080 at 1080p, so where the log
    # remembers what the existing file started as, that beats its current height.
    entry = log.get(existing.name, {})
    old_height = entry.get("source_height") or 0
    old_duration = entry.get("duration") or 0.0
    if not old_height or not old_duration:
        probed = probe(existing)
        if probed is None:
            return core.AMBIGUOUS, existing
        old_height = old_height or probed[0]
        old_duration = old_duration or probed[1]

    verdict = core.duplicate_verdict(height, duration, old_height, old_duration)
    if policy == "review" and verdict != core.DOWNGRADE:
        return core.AMBIGUOUS, existing
    return verdict, existing


def park_for_review(root: Path, video: Path, reason: str) -> Path:
    review = root / REVIEW_DIR
    review.mkdir(exist_ok=True)
    dest = unique_path(review / video.name)
    shutil.move(str(video), str(dest))
    warn(f"{video.name} → {REVIEW_DIR}/ ({reason})")
    return dest


def process(video: Path, root: Path, log: dict, plan: EncoderPlan, args,
            seen: set[str]) -> dict:
    """Optimise, tag and sort one file. Returns a result row for the summary table."""
    name = video.name

    probed = probe(video)
    if probed is None:
        warn(f"Skipping (ffprobe could not read it): {name}")
        return {"file": name, "status": "skipped", "reason": "unreadable"}
    src_height, src_duration, src_depth = probed

    # 10-bit tracks the source by default, so HDR and 10-bit grades aren't flattened to
    # 8-bit — but a backend without a 10-bit encoder quietly stays 8-bit rather than
    # jumping to different hardware, so say when that happens. Decided before anything
    # else, because both the filename and the "is this already done?" test depend on the
    # depth this run would produce.
    if plan is None:
        ten_bit = src_depth > 8          # no encoder to consult; display only
    else:
        ten_bit = plan.wants_10bit(src_depth)
        if ten_bit and not plan.has_10bit():
            warn(
                f"{name}: {src_depth}-bit source but {plan.backend.name} has no 10-bit "
                f"encoder here — encoding 8-bit"
            )
            ten_bit = False
    target_depth = 10 if ten_bit else 8

    # Compared against what *this* invocation would produce, not against the file's own
    # content — so `-B 8` re-encodes a file already tagged 10bit instead of calling it done.
    skip_encode = not args.force and already_optimised(video, src_height, target_depth, seen)

    tagged_stem = core.apply_tags(video.stem, src_height, target_depth)
    trip, dest_folder = plan_destination(root, tagged_stem)

    # Pre-flight: reject a clear downgrade before spending an hour encoding it.
    verdict, existing = resolve_duplicate(
        root, video, src_height, src_duration, dest_folder, log, args.on_duplicate,
    )
    if verdict == core.DOWNGRADE and args.on_duplicate != "ignore":
        if args.dry_run:
            info(f"{name}: downgrade of {existing.name} — would park in {REVIEW_DIR}/")
        else:
            park_for_review(root, video, f"downgrade of {existing.name}")
            update_log(root, log, name, {
                "status": "review", "reason": "downgrade",
                "resolution": core.resolution_label(src_height),
                "source_height": src_height, "duration": src_duration,
                "compared_with": existing.name, "ts": datetime.now().isoformat(),
            })
        return {"file": name, "status": "review", "reason": "downgrade"}

    preset_stem = core.select_preset(src_height, args.preset_1080, args.preset_720)

    if args.dry_run:
        if not trip:
            dest_desc = "(stays in root — no trip field)"
        elif dest_folder is not None:
            # Show the folder that was actually matched, so a case-insensitive hit on an
            # existing folder is distinguishable from creating a near-duplicate of it.
            dest_desc = f"{dest_folder.relative_to(root)}/  [dim](existing)[/]"
        else:
            dest_desc = f"{trip}/  [yellow](would be created)[/]"
        extra = f"  [{verdict} vs {existing.name}]" if verdict else ""
        depth_note = (
            f", {src_depth}-bit source, encoder not checked"
            if plan is None
            else f", {src_depth}-bit → {'10' if ten_bit else '8'}-bit"
        )
        action = (
            "already optimised — sort only"
            if skip_encode
            else f"{preset_stem}  ({core.resolution_label(src_height)} source{depth_note})"
        )
        info(
            f"{name}\n"
            f"    encode   {action}\n"
            f"    rename   {tagged_stem}{'.mp4' if not skip_encode else video.suffix}\n"
            f"    move to  {dest_desc}{extra}"
        )
        return {"file": name, "status": "dry-run"}

    if skip_encode:
        # Seen before and already carrying the right tag — go straight to sorting.
        final = video
        orig_size = comp_size = video.stat().st_size
        out_height, out_duration = src_height, src_duration
        info(f"{name}  [dim]already optimised — sorting only[/]")
        return _finish(video, final, root, log, plan, args,
                       orig_size, comp_size, src_height, out_height, out_duration,
                       src_depth)

    # ── Encode ────────────────────────────────────────────────────
    if not wait_stable(video):
        info(f"Skipping (still being written): {name}")
        return {"file": name, "status": "skipped", "reason": "still changing"}

    orig_size = video.stat().st_size
    src_processing = video.parent / f"{name}.processing"
    tmp = root / f"{tagged_stem}.mp4.tmp"

    try:
        video.rename(src_processing)
    except OSError:
        warn(f"Could not lock for processing: {name}")
        return {"file": name, "status": "failed", "original_size": orig_size}

    preset_file, preset_label = load_preset(preset_stem)

    with progress_bar() as progress:
        task = progress.add_task(
            f"[cyan]{name}[/]  [dim]{fmt_size(orig_size)} · {preset_stem}[/]", total=100,
        )
        succeeded = transcode(
            src_processing, tmp, preset_file, preset_label, progress, task,
            extra_args=plan.args_for(preset_stem, ten_bit=ten_bit),
            low_priority=not args.no_nice,
        )
        # An auto-selected backend that fails gets one CPU retry; an explicitly requested
        # one does not — the same rule _common/encoders.py follows.
        if not succeeded and plan.auto_selected and plan.backend.name != "cpu":
            warn(f"{plan.backend.name} encode failed for {name} — retrying on CPU")
            if tmp.exists():
                tmp.unlink()
            succeeded = transcode(
                src_processing, tmp, preset_file, preset_label, progress, task,
                extra_args=plan.args_for(preset_stem, force_cpu=True, ten_bit=ten_bit),
                low_priority=not args.no_nice,
            )

    if not succeeded or not tmp.exists():
        if tmp.exists():
            tmp.unlink()
        src_processing.rename(video)
        failures = log.get(name, {}).get("failures", 0) + 1
        warn(
            f"Transcode failed: {name}"
            + (f" — giving up after {failures} attempts" if failures >= MAX_FAILURES else "")
        )
        update_log(root, log, name, {
            "status": "failed", "failures": failures,
            "ts": datetime.now().isoformat(),
        })
        return {"file": name, "status": "failed", "original_size": orig_size}

    comp_size = tmp.stat().st_size
    pct = (1 - comp_size / orig_size) * 100 if orig_size else 0

    if comp_size < orig_size:
        final = unique_path(root / f"{tagged_stem}.mp4")
        tmp.rename(final)
        move_to_trash(str(src_processing))
        console.print(
            f"  [green]✓[/] {name}  "
            f"[dim]{fmt_size(orig_size)} → {fmt_size(comp_size)} ({pct:+.1f}%)[/]"
        )
    else:
        # The original was already better than we can do — keep it, just retag it.
        tmp.unlink()
        src_processing.rename(video)
        final = video
        wanted = video.with_name(f"{tagged_stem}{video.suffix}")
        if wanted != video:
            final = unique_path(wanted)
            video.rename(final)
        comp_size = orig_size
        console.print(f"  [yellow]≈[/] {name}  [dim]original kept ({pct:+.1f}%)[/]")

    # ── Re-probe, since the encode decides the real resolution ────
    reprobed = probe(final)
    out_height, out_duration = reprobed[:2] if reprobed else (src_height, src_duration)
    out_depth = reprobed[2] if reprobed else src_depth

    return _finish(video, final, root, log, plan, args,
                   orig_size, comp_size, src_height, out_height, out_duration,
                   out_depth)


def _finish(video: Path, final: Path, root: Path, log: dict, plan: EncoderPlan, args,
            orig_size: int, comp_size: int, src_height: int,
            out_height: int, out_duration: float, out_depth: int = 8) -> dict:
    """Retag to the measured resolution, resolve duplicates, and sort.

    Shared by the encode path and the already-optimised path so a file that comes back to
    the root reaches exactly the same destination as one that was just transcoded.
    """
    name = video.name

    retagged = core.apply_tags(final.stem, out_height, out_depth)
    if retagged != final.stem:
        renamed = unique_path(final.with_name(f"{retagged}{final.suffix}"))
        final.rename(renamed)
        final = renamed

    entry = {
        "status": "sorted",
        "resolution": core.resolution_label(out_height),
        "source_height": src_height,
        "duration": out_duration,
        "bit_depth": out_depth,
        "backend": plan.backend.name,
        "original_size": orig_size,
        "compressed_size": comp_size,
        "ts": datetime.now().isoformat(),
    }

    # ── Duplicate resolution, re-decided on the finished file ─────
    trip, dest_folder = plan_destination(root, final.stem)
    verdict, existing = resolve_duplicate(
        root, final, out_height, out_duration, dest_folder, log, args.on_duplicate,
    )

    if verdict and not core.wins(verdict) and args.on_duplicate != "ignore":
        park_for_review(root, final, f"{verdict} vs {existing.name}")
        entry.update(status="review", reason=verdict, compared_with=existing.name)
        update_log(root, log, final.name, entry)
        return {"file": name, "status": "review", "reason": verdict,
                "original_size": orig_size, "compressed_size": comp_size}

    # ── Sort ──────────────────────────────────────────────────────
    if not trip:
        if log.get(final.name, {}).get("status") != "awaiting_trip":
            warn(f"No trip field, leaving in root: {final.name}")
        entry["status"] = "awaiting_trip"
        update_log(root, log, final.name, entry)
        return {"file": name, "status": "awaiting_trip",
                "original_size": orig_size, "compressed_size": comp_size}

    if dest_folder is None:
        dest_folder = root / trip
        dest_folder.mkdir(parents=True, exist_ok=True)

    if verdict and core.wins(verdict):
        move_to_trash(str(existing))
        ok(f"Replaced {existing.name} ({verdict})")
        entry["replaced"] = existing.name

    dest = dest_folder / final.name
    if dest.exists():
        warn(f"Already exists, leaving in root: {dest}")
        entry["status"] = "skipped"
        entry["reason"] = "destination exists"
        update_log(root, log, final.name, entry)
        return {"file": name, "status": "skipped", "reason": "destination exists",
                "original_size": orig_size, "compressed_size": comp_size}

    shutil.move(str(final), str(dest))
    ok(f"{final.name} → {trip}/")

    entry["trip"] = trip
    entry["dest"] = str(dest.relative_to(root))
    update_log(root, log, final.name, entry)

    return {"file": name, "status": "sorted", "trip": trip,
            "original_size": orig_size, "compressed_size": comp_size}


# ── Reporting ─────────────────────────────────────────────────────────

def show_report(root: Path, log: dict) -> None:
    below = {
        k: v for k, v in log.items()
        if core.resolution_height(v.get("resolution", "")) and
        core.resolution_height(v.get("resolution", "")) < 1080
    }
    if below:
        tbl = Table(
            title="Below 1080p — candidates for upscaling",
            box=box.ROUNDED, title_style="bold cyan",
        )
        tbl.add_column("File", style="cyan", no_wrap=True)
        tbl.add_column("Resolution", justify="center")
        tbl.add_column("Depth", justify="center")
        tbl.add_column("Location")
        for fname, entry in sorted(below.items()):
            depth = entry.get("bit_depth")
            tbl.add_row(
                fname, entry.get("resolution", "?"),
                f"{depth}-bit" if depth else "—",
                entry.get("dest", "—"),
            )
        console.print(tbl)
    else:
        info("Nothing below 1080p on record.")

    review = root / REVIEW_DIR
    parked = sorted(review.iterdir()) if review.is_dir() else []
    parked = [p for p in parked if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    if parked:
        tbl = Table(
            title=f"Waiting in {REVIEW_DIR}/", box=box.ROUNDED, title_style="bold yellow",
        )
        tbl.add_column("File", style="yellow", no_wrap=True)
        tbl.add_column("Reason")
        tbl.add_column("Compared with")
        for p in parked:
            entry = log.get(p.name, {})
            tbl.add_row(p.name, entry.get("reason", "—"), entry.get("compared_with", "—"))
        console.print()
        console.print(tbl)


def show_summary(results: list[dict]) -> None:
    if not results:
        return
    counts: dict[str, int] = {}
    total_orig = total_comp = 0
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        total_orig += r.get("original_size", 0)
        total_comp += r.get("compressed_size", 0)

    parts = [f"{n} {status}" for status, n in sorted(counts.items())]
    line = ", ".join(parts)
    if total_orig:
        net = (1 - total_comp / total_orig) * 100
        line += f"  ({fmt_size(total_orig)} → {fmt_size(total_comp)}, {net:+.1f}%)"
    console.print()
    info(line)


# ── Entry point ───────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="optimiselib",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("root", nargs="?", default=".", help="Library root to watch (default: .)")
    ap.add_argument("-t", "--interval", type=int, default=30,
                    help="Seconds between folder scans (default: 30)")
    ap.add_argument("-O", "--once", action="store_true",
                    help="Process existing files and exit (no continuous watch)")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="Show the planned rename, destination and duplicate verdict; change nothing")
    ap.add_argument("-r", "--report", action="store_true",
                    help="List files below 1080p and the review queue, then exit")
    ap.add_argument("-F", "--force", action="store_true",
                    help="Re-process files the log has already marked done")
    ap.add_argument("-g", "--gpu", default=None, choices=core.GPU_CHOICES,
                    help="Encoder backend (default: auto; env OPTIMISELIB_GPU)")
    ap.add_argument("-Q", "--quality", type=float, default=None,
                    help="Override the preset's quality value")
    ap.add_argument("-B", "--bit-depth", default="auto", choices=core.BIT_DEPTH_CHOICES,
                    help="auto matches the source (10-bit stays 10-bit, 8-bit is not "
                         "inflated); 8 or 10 forces it. Default: auto")
    ap.add_argument("-H", "--hours", default=None,
                    help="Only start encodes inside this window, e.g. 01:00-07:00")
    ap.add_argument("-L", "--max-load", type=float, default=None,
                    help="Skip a poll while the 1-minute load average exceeds this (POSIX)")
    ap.add_argument("-d", "--on-duplicate", default="auto",
                    choices=("auto", "review", "ignore"),
                    help="When the library already has this video: auto (replace on a clear win, park anything ambiguous in _review/), review (park every duplicate), ignore (file both copies). Default: auto")
    ap.add_argument("-N", "--no-nice", action="store_true",
                    help="Don't de-prioritise the encoder process")
    ap.add_argument("-p", "--preset-1080", default="hw-1080",
                    help="Preset for sources above 720p (default: hw-1080)")
    ap.add_argument("-q", "--preset-720", default="hw-720",
                    help="Preset for sources at 720p or below (default: hw-720)")
    return ap


def main() -> None:
    args = build_parser().parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        die(f"Not a directory: {root}")

    log = load_log(root)

    if args.report:
        show_report(root, log)
        return

    require_tool("ffprobe", "part of ffmpeg")
    # A dry run never encodes, so it doesn't need HandBrake — that's what makes it usable
    # for checking naming and sorting on a machine that isn't the server. When HandBrake
    # *is* present the plan is still built, so the dry run reports the encoder and bit
    # depth it would really use rather than guessing.
    handbrake_present = shutil.which("HandBrakeCLI") is not None
    if not args.dry_run and not handbrake_present:
        require_tool("HandBrakeCLI", "https://handbrake.fr/downloads2.php")

    window = None
    if args.hours:
        try:
            window = core.parse_hours(args.hours)
        except ValueError as exc:
            die(f"Bad --hours value: {exc}")

    plan = build_encoder_plan(
        args, [args.preset_1080, args.preset_720],
    ) if handbrake_present else None

    banner = Table.grid(padding=(0, 2))
    banner.add_column(style="bold")
    banner.add_column()
    banner.add_row("Library", str(root))
    if plan is not None:
        banner.add_row("Encoder", f"{plan.backend.name}  [dim]({plan.backend.encoder})[/]")
        if plan.has_10bit():
            depth = ("matches source" if args.bit_depth == "auto"
                     else f"forced {args.bit_depth}-bit")
            banner.add_row("Bit depth", f"{depth}  [dim]({plan.backend.encoder_10bit})[/]")
        else:
            banner.add_row("Bit depth", "[yellow]8-bit only — no 10-bit encoder here[/]")
    if args.quality is not None:
        banner.add_row("Quality", f"{args.quality}  [dim](override)[/]")
    banner.add_row("Presets", f"{args.preset_1080} / {args.preset_720}")
    banner.add_row("Mode", "one-shot" if args.once else f"poll every {args.interval}s")
    if window:
        banner.add_row("Hours", args.hours)
    if args.max_load is not None:
        banner.add_row("Max load", str(args.max_load))
    if args.dry_run:
        banner.add_row("Dry run", "[yellow]nothing will be changed[/]")

    console.print(Panel(banner, title="[bold cyan]optimiselib[/]",
                        border_style="cyan", expand=False))
    console.print()

    if not args.dry_run:
        recover_interrupted(root)

    try:
        first_scan = True
        while True:
            if not core.within_hours(window):
                delay = core.seconds_until(window)
                info(f"Outside the encode window — sleeping {delay // 60} min")
                time.sleep(min(delay, 3600))
                continue

            if not load_ok(args.max_load):
                info(f"Load above {args.max_load} — waiting {args.interval}s")
                time.sleep(args.interval)
                continue

            # Anything sitting in the root is fair game: it's either new, or one you moved
            # back from a folder of your own to be sorted. already_optimised() is what
            # stops the latter being re-encoded, so this filter only has to skip the two
            # cases that would otherwise spin — a name still missing its trip field, and a
            # file that keeps failing to transcode.
            videos = [
                v for v in scan(root)
                if args.force or should_process(v, log)
            ]
            seen = seen_identities(log)

            if not videos:
                if args.once:
                    if first_scan:
                        info("Nothing new to process.")
                    break
                first_scan = False
                time.sleep(args.interval)
                continue

            console.print(f"[bold]Found {len(videos)} file(s)[/]\n")
            results = [process(v, root, log, plan, args, seen) for v in videos]
            show_summary(results)

            if args.once or args.dry_run:
                break

            console.print(f"\n[dim]Watching (every {args.interval}s)… Ctrl+C to stop[/]")
            time.sleep(args.interval)
            first_scan = False

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Stopped.[/]")


if __name__ == "__main__":
    main()
