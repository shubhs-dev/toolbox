"""
wallready — Prepare every image in a folder for use as a wallpaper, via ImageMagick.

Requires:
    magick (ImageMagick 7)

Usage:
    wallready
    wallready ~/Pictures/walls
    wallready "arc raiders"        # ...and rename everything arc-raiders-<id>.jpg
    wallready -p "arc raiders" ~/Pictures/walls
    wallready -n                   # dry run — show the plan, change nothing
    wallready -s 1440p             # target 2560x1440 instead of 4K
    wallready -s 3440x1440         # or any WIDTHxHEIGHT
    wallready -q 95 -S             # higher JPEG quality, strip metadata
    wallready -k                   # move originals into originals/ instead of trashing

Every image becomes a JPG at the target size (3840x2160 by default):

  * Bigger than the target — scaled down until it just covers the target, then
    centre-cropped to it. A 16:9 source is a pure downscale; any other aspect ratio
    loses the excess off its long edge, half from each side.
  * Exactly the target size already — re-encoded to JPG, or left alone if it already is
    one (a JPG stored sideways under an EXIF orientation tag is re-encoded, not skipped,
    so the rotation is baked in for viewers that ignore the tag).
  * Portrait, against a landscape target (or landscape against a portrait one) — moved
    untouched into portrait/, since no crop of a tall photo makes a usable wide wallpaper.
  * Smaller than the target on either axis — moved untouched into too-small/.
    Nothing is ever upscaled.

The lone argument is the folder to work in when it names one, and otherwise a prefix to
rename every image after: `wallready "arc raiders"` turns `1402962.jpg` into
`arc-raiders-1402962.jpg` and `wallhaven-e204cm.jpg` into `arc-raiders-e204cm.jpg`. The id
kept on the end is whatever the download name already carried — a run of digits, or a
letters-and-digits token like `e204cm` — and a name with nothing identifiable in it gets a
fresh random one instead. A file already named `<prefix>-<id>` keeps the name it has, so
re-running doesn't stack prefixes. -p/--prefix takes both a prefix and a folder, and is the
way to pass a prefix that happens to name a real folder. Images set aside into portrait/ or
too-small/ are renamed too; the originals keep their download name.

EXIF orientation is applied before anything is measured or cropped, transparency is
flattened onto --background (JPEG has no alpha), and CMYK or wide-gamut sources are
converted to sRGB. Each original goes to the Trash once its wallpaper has been written
successfully; -k/--keep moves it into originals/ instead. Subfolders aren't scanned.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from rich import box
from rich.table import Table

from toolboxcli._common.console import console, die, error, info, ok, warn
from toolboxcli._common.images import IMAGE_EXTS, normalize_ext
from toolboxcli._common.output import is_same_file
from toolboxcli._common.tooling import require_tool
from toolboxcli._common.trash import move_to_trash
from toolboxcli.wallready.core import (
    DEFAULT_TARGET,
    SET_ASIDE,
    SIZE_PRESETS,
    Action,
    Plan,
    classify,
    convert_args,
    is_rotated,
    mismatch_dir,
    oriented_size,
    parse_size,
    rename_stem,
    slugify,
)

TOO_SMALL_DIR = "too-small"
ORIGINALS_DIR = "originals"

TMP_PREFIX = ".wallready."


class Outcome(Enum):
    """How one image's wallpaper turned out. A name collision is a skip, not a failure —
    nothing went wrong, there was just already a file in the way."""

    WRITTEN = "written"
    SKIPPED = "skipped"
    FAILED = "failed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallready",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target", nargs="?", default=None, metavar="DIRECTORY|PREFIX",
        help="A folder of images to prepare (default: the current directory), or — when it "
             "doesn't name one — a prefix to rename every image after, as <prefix>-<id>",
    )
    parser.add_argument(
        "-p", "--prefix", default=None, metavar="NAME",
        help="Rename every image <prefix>-<id>, keeping the id its download name carried "
             "and generating one when it carried none",
    )
    parser.add_argument(
        "-s", "--size", default="4k", metavar="SIZE",
        help=f"Target size: a preset ({', '.join(SIZE_PRESETS)}) or WIDTHxHEIGHT "
             f"(default: 4k = {DEFAULT_TARGET[0]}x{DEFAULT_TARGET[1]})",
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=92, metavar="N",
        help="JPEG quality 1-100 (default: 92)",
    )
    parser.add_argument(
        "-b", "--background", default="white", metavar="COLOR",
        help="Fill colour for transparent areas, which JPEG can't store (default: white)",
    )
    parser.add_argument(
        "-k", "--keep", action="store_true",
        help=f"Move originals into {ORIGINALS_DIR}/ instead of the Trash",
    )
    parser.add_argument(
        "-S", "--strip", action="store_true",
        help="Strip metadata (EXIF, colour profiles) from the finished wallpapers",
    )
    parser.add_argument(
        "-f", "--force", action="store_true",
        help="Replace an existing .jpg of the same name (it goes to the Trash)",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Show what would happen without changing anything",
    )
    return parser


def looks_like_path(value: str) -> bool:
    """True when the lone argument is meant as a folder rather than a prefix.

    An argument that already names a folder obviously is one; beyond that it's anything
    spelled like a path — `~/walls`, `./walls`, `..`, `walls/`. A mistyped folder name with
    a separator in it still fails loudly as a missing directory instead of being taken for
    a prefix and quietly run against the current one.
    """
    if Path(value).expanduser().is_dir():
        return True
    if value in (".", "..") or value.startswith("~"):
        return True
    return "/" in value or "\\" in value


def resolve_target(args: argparse.Namespace) -> tuple[Path, str | None]:
    """(folder to work in, prefix to rename after) from the positional and -p/--prefix."""
    directory, prefix = ".", args.prefix

    if args.target is not None:
        if prefix is None and not looks_like_path(args.target):
            prefix = args.target
        else:
            directory = args.target

    if prefix is not None:
        slug = slugify(prefix)
        if not slug:
            die(f"prefix '{prefix}' has no letters or digits to make a filename out of")
        prefix = slug

    return Path(directory).expanduser(), prefix


def reserve_name(folder: Path, stem: str, suffix: str, src: Path, taken: set[Path]) -> Path:
    """A free path for `stem + suffix` in `folder`, numbering it if that name is spoken for.

    Two downloads can carry the same id, and two freshly generated ids can collide, so a
    renamed file never just overwrites what's already there — `-2`, `-3` and so on are
    appended until the name is free. `src` doesn't collide with itself: a file re-run under
    the prefix it already has keeps its name rather than gaining a number every time.
    """
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate in taken or (candidate.exists() and not is_same_file(src, candidate)):
        candidate = folder / f"{stem}-{n}{suffix}"
        n += 1
    taken.add(candidate)
    return candidate


@contextmanager
def readable_path(src: Path) -> Iterator[Path]:
    """Yield a path ImageMagick will read as a plain filename.

    A trailing `[...]` in a filename is a frame/scene spec to ImageMagick, so a browser's
    `wallpaper[1].jpg` gets looked up as `wallpaper.jpg` and fails. For those names only,
    work from a temp copy whose name has no brackets.
    """
    if "[" not in src.name and "]" not in src.name:
        yield src
        return

    fd, tmp_name = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=src.suffix, dir=src.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(src, tmp)
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)


def identify(path: Path) -> tuple[int, int, str, int] | None:
    """(width, height, EXIF orientation, frame count) as ImageMagick reads the file, or
    None if it can't be read. Multi-frame files (animated GIF/WebP) print a line per
    frame; only the first one is a wallpaper candidate."""
    try:
        result = subprocess.run(
            ["magick", "identify", "-format", "%w %h %[orientation]\\n", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None

    parts = lines[0].split()
    if len(parts) < 2:
        return None
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    orientation = parts[2] if len(parts) > 2 else "Undefined"
    return width, height, orientation, len(lines)


def blocked_reason(
    src: Path, dest: Path, args: argparse.Namespace, originals: Path
) -> str | None:
    """Why `src`'s wallpaper can't be written to `dest`, or None if it can.

    Checked up front rather than part-way through, so nothing destructive happens on a run
    that can't finish, and the dry run predicts the same skips the real run performs. Under
    a prefix the first test never fires — `reserve_name` has already picked a free name.
    """
    if dest.exists() and not is_same_file(src, dest) and not args.force:
        return f"'{dest.name}' already exists — use -f/--force to replace it"
    if args.keep and (originals / src.name).exists():
        return f"'{originals.name}/{src.name}' already exists"
    return None


def set_aside(src: Path, dest: Path, dry_run: bool) -> bool:
    """Move an image out of the way, untouched, to `dest`."""
    folder = dest.parent
    # Checked before the dry-run branch, so -n reports the collision rather than
    # promising a move that the real run would refuse.
    if dest.exists():
        warn(f"Skipping '{src.name}': '{folder.name}/{dest.name}' already exists")
        return False
    if dry_run:
        info(rf"\[dry-run] {src.name}  →  {folder.name}/{dest.name}")
        return True
    folder.mkdir(exist_ok=True)
    shutil.move(str(src), str(dest))
    return True


def trash(path: Path) -> bool:
    """Move a file to the Trash, reporting rather than raising when no trash mechanism is
    available — one unreachable Trash shouldn't abort a whole folder, and a file is never
    plain-deleted as a fallback."""
    try:
        move_to_trash(path)
        return True
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        error(f"Couldn't move '{path.name}' to the Trash: {e}")
        return False


def retire_original(src: Path, originals: Path, keep: bool) -> bool:
    """Get the original out of the way now its wallpaper has been written: into
    originals/ with -k/--keep, otherwise to the Trash. Never a plain delete.

    False means it couldn't be retired, and the caller must leave the original alone —
    a JPG resized in place writes over its own path, so letting that go ahead would
    destroy the very file -k/--keep promised to preserve.
    """
    if not keep:
        return trash(src)

    originals.mkdir(exist_ok=True)
    dest = originals / src.name
    if dest.exists():
        warn(f"Skipping '{src.name}': '{originals.name}/{dest.name}' already exists")
        return False
    shutil.move(str(src), str(dest))
    return True


def rename_only(src: Path, dest: Path, dry_run: bool) -> None:
    """Give a wallpaper that needs no work the name the prefix asks for.

    No encode, so no original to retire and nothing for -k/--keep to preserve: the file
    that comes out is the file that went in, under another name.
    """
    if dry_run:
        info(rf"\[dry-run] {src.name}  →  {dest.name}")
        return
    shutil.move(str(src), str(dest))


def write_wallpaper(
    src: Path,
    read_from: Path,
    dest: Path,
    plan: Plan,
    args: argparse.Namespace,
    originals: Path,
    multi_frame: bool,
) -> Outcome:
    """Produce `src`'s wallpaper at `dest`, then retire the original.

    The encode goes to a temp file in the same folder and only replaces the destination
    once ImageMagick has succeeded, so a failed or interrupted run can't leave a
    half-written wallpaper where the original used to be.
    """
    # Not `dest == src`: on a case-insensitive filesystem a .JPG source and its lowercase
    # .jpg destination are the same file, and treating them as two would trash the source
    # as "the old destination" and leave nothing to convert.
    in_place = is_same_file(src, dest)

    blocked = blocked_reason(src, dest, args, originals)
    if blocked:
        warn(f"Skipping '{src.name}': {blocked}")
        return Outcome.SKIPPED

    # Read the original's permissions before it's retired — when the wallpaper replaces a
    # JPG in place there's nothing left to read them from afterwards.
    mode = src.stat().st_mode & 0o7777

    # The temp name deliberately doesn't reuse the source stem: that stem may contain the
    # brackets ImageMagick would read as a frame spec on the way out.
    fd, tmp_name = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=".jpg", dir=src.parent)
    os.close(fd)
    tmp = Path(tmp_name)

    try:
        result = subprocess.run(
            [
                "magick",
                f"{read_from}[0]" if multi_frame else str(read_from),
                *convert_args(plan, args.quality, args.background, args.strip),
                str(tmp),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not tmp.stat().st_size:
            detail = (result.stderr or "").strip().splitlines()
            error(f"Failed to convert '{src.name}'" + (f": {detail[0]}" if detail else ""))
            return Outcome.FAILED

        # The original goes first: it has to, when it and the result are the same path (a
        # JPG resized in place), and doing it before the old destination is trashed means a
        # run that can't retire the original hasn't already thrown the destination away.
        if not retire_original(src, originals, args.keep):
            return Outcome.SKIPPED

        if dest.exists() and not in_place:
            if not trash(dest):
                return Outcome.FAILED
            info(f"Moved the old {dest.name} to the Trash")
        # mkstemp makes its file owner-only; without this the wallpaper would come out
        # 0600 whatever the original was, which breaks a shared or served folder.
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)

    return Outcome.WRITTEN


def main() -> None:
    args = build_parser().parse_args()

    require_tool("magick", "ImageMagick 7: https://imagemagick.org/script/download.php")

    if not 1 <= args.quality <= 100:
        die("--quality must be a number between 1 and 100")

    target = parse_size(args.size)
    tw, th = target

    root, prefix = resolve_target(args)
    if not root.is_dir():
        die(f"not a directory: {root}")

    images = sorted(
        p for p in root.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lstrip(".").lower() in IMAGE_EXTS
    )
    if not images:
        warn(f"No images found in {root}")
        return

    aside_dirs = {
        Action.MISMATCH: root / mismatch_dir(target),
        Action.TOO_SMALL: root / TOO_SMALL_DIR,
    }
    originals = root / ORIGINALS_DIR

    info(f"Preparing {len(images)} image(s) in {root} for {tw}x{th} wallpapers")
    if prefix:
        info(f"Renaming each one '{prefix}-<id>'")
    if args.dry_run:
        warn("Dry-run mode — no files will be changed.")

    counts: Counter[str] = Counter()
    # Names this run has already promised to other images. The dry run leans on it
    # entirely, since none of the files it reports are actually there to be collided with.
    taken: set[Path] = set()

    for src in images:
        with readable_path(src) as read_from:
            probed = identify(read_from)
            if probed is None:
                error(f"Couldn't read '{src.name}' — skipping")
                counts["failed"] += 1
                continue

            raw_w, raw_h, orientation, frames = probed
            width, height = oriented_size(raw_w, raw_h, orientation)
            plan = classify(
                width, height, target,
                is_jpeg=normalize_ext(src.suffix) == "jpg",
                rotated=is_rotated(orientation),
            )

            label = f"{src.name}: {plan.describe()}"

            # Set aside before a .jpg name is reserved: these files keep their own format
            # and land in a subfolder, so they need a different name reserved, not this one.
            if plan.action in SET_ASIDE:
                folder = aside_dirs[plan.action]
                dest = (
                    reserve_name(folder, rename_stem(src.stem, prefix), src.suffix, src, taken)
                    if prefix
                    else folder / src.name
                )
                warn(label)
                if set_aside(src, dest, args.dry_run):
                    counts[plan.action.value] += 1
                else:
                    counts["skipped"] += 1
                continue

            dest = (
                reserve_name(root, rename_stem(src.stem, prefix), ".jpg", src, taken)
                if prefix
                else src.with_suffix(".jpg")
            )

            if plan.action is Action.READY:
                if prefix and dest != src:
                    rename_only(src, dest, args.dry_run)
                    ok(f"{label}  →  {dest.name}")
                    counts["renamed"] += 1
                else:
                    info(f"{label} — nothing to do")
                    counts["ready"] += 1
                continue

            if args.dry_run:
                blocked = blocked_reason(src, dest, args, originals)
                if blocked:
                    warn(f"Skipping '{src.name}': {blocked}")
                    counts["skipped"] += 1
                else:
                    info(rf"\[dry-run] {label}  →  {dest.name}")
                    counts[plan.action.value] += 1
                continue

            outcome = write_wallpaper(src, read_from, dest, plan, args, originals, frames > 1)
            if outcome is Outcome.WRITTEN:
                ok(f"{label}  →  {dest.name}")
                counts[plan.action.value] += 1
            else:
                counts[outcome.value] += 1

    summary(counts, target, aside_dirs, args.dry_run)


def summary(
    counts: Counter[str],
    target: tuple[int, int],
    aside_dirs: dict[Action, Path],
    dry_run: bool,
) -> None:
    tw, th = target
    rows = [
        (f"Resized to {tw}x{th}", counts[Action.RESIZE.value]),
        ("Re-encoded to JPG", counts[Action.CONVERT.value]),
        ("Already wallpaper-ready", counts["ready"]),
        ("Renamed only", counts["renamed"]),
        (f"Wrong shape → {aside_dirs[Action.MISMATCH].name}/", counts[Action.MISMATCH.value]),
        (f"Too small → {aside_dirs[Action.TOO_SMALL].name}/", counts[Action.TOO_SMALL.value]),
        ("Skipped", counts["skipped"]),
        ("Failed", counts["failed"]),
    ]

    table = Table(
        title="wallready — dry run" if dry_run else "wallready",
        box=box.ROUNDED, title_style="bold yellow",
    )
    table.add_column("Outcome", style="green")
    table.add_column("Files", justify="right")
    for name, count in rows:
        if count:
            table.add_row(name, str(count))

    console.print()
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
