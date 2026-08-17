"""
kavitaname — Rename manga/manhwa/book files for Kavita server compatibility.

Scans a series folder, parses each filename for volume/chapter/special
information, and renames the files into the form Kavita's scanner reads back
most reliably. Unlike the previous version, filenames do not have to already
look like "Chapter 12.cbz" — scanlator releases are parsed directly:

    [Hidoi]_Amaenaideyo_MS_vol01_chp02.rar  ->  Series c002 (v01).rar
    Series - Vol. 04 Ch. 054.5.cbz          ->  Series c054.5 (v04).cbz
    Series 018.5 (2019) (Digital).cbz       ->  Series c018.5.cbz
    Series v03 Omake.cbz                    ->  Specials/Series SP01 - Omake.cbz

Output format:
    chapter + volume:  {Series} c{ch:03d} (v{vol:02d}).cbz
    chapter only:      {Series} c{ch:03d}.cbz
    whole volume:      {Series} v{vol:02d}.cbz
    prologue:          {Series} c000.N.cbz
    special:           Specials/{Series} SP{NN} - {Title}.cbz

Specials are detected the way Kavita detects them: an existing SP## marker
always wins, and a keyword (Omake, Extra, Side Story, One-Shot, TPB, ...) only
counts when the filename carries no volume or chapter number — so
"v20 c171-180 Omake" stays a chapter. Files that become specials are moved into
a Specials/ subfolder, which is the layout Kavita's scanner is tuned for.

For .cbz/.zip archives, ComicInfo.xml inside the archive is read as a fallback
and fills in only what the filename left out — Series, Volume, Number, Title,
and a Format that marks the file special. Values parsed from the filename are
never overridden. .cbr/.rar are RAR archives and are skipped for this (no extra
dependency); their filenames are still parsed normally.

The series name defaults to the folder name, which is what Kavita itself falls
back to. Override the value with -s, or the source with -S.

A volume map (-m) can supply chapter-to-volume numbers for manga. Two formats
are accepted and auto-detected: JSON, as in mergemanga's bundled
volume_map.json ([{"volume": 1, "start": 1, "end": 8}, ...]), and the legacy
pipe-delimited form (vol|first_chapter|last_chapter|Title, with '#' comments).
Chapters absent from the map are named without volume info rather than skipped.

Cover images (cover.jpg, folder.png, !cover.*) and OS junk files are left alone.

Usage:
    kavitaname [options] [directory]
    kavitaname manga
    kavitaname -s "One Piece" -m volumes.json manga
    kavitaname -n manhwa ~/manga/Solo\\ Leveling
    kavitaname -r manga ~/manga
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Callable, Iterable

from toolboxcli._common.console import console, die, info, ok, warn
from toolboxcli._common.pathsafe import sanitize_for_path
from toolboxcli.kavitaname.core import (
    EXTS_FOR_KIND,
    SPECIAL_KEYWORDS,
    SPECIALS_DIR,
    ChapterInfo,
    is_cover_image,
    parse_stem,
    process_file,
)

JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}

# Only zip-based containers can be read with the stdlib. .cbr/.rar are RAR and
# .cb7/.7z are 7z — reading those would mean a new dependency, which CLAUDE.md
# rules out for exactly this reason (mergemanga dropped 7z for the same logic:
# CBZ is already a zip).
ZIP_EXTS = {"cbz", "zip", "cbt", "epub"}


def is_junk(p: Path) -> bool:
    return p.name in JUNK_NAMES or p.name.startswith("._")


def _natural_key(name: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


# ---------------------------------------------------------------------------
# volume map
# ---------------------------------------------------------------------------


def load_volume_map(vol_file: Path) -> dict[int, int]:
    """Chapter number -> volume number, from JSON or the legacy pipe format.

    The format is auto-detected from the content rather than the extension:
    the old docs promised mergemanga compatibility while the parser only
    understood pipes, and mergemanga's data file is JSON. Accepting both means
    neither the old files nor the documented ones break.
    """
    raw = vol_file.read_text(encoding="utf-8", errors="replace")
    stripped = raw.lstrip()
    if stripped.startswith(("[", "{")):
        return _load_json_map(raw, vol_file)
    return _load_pipe_map(raw)


def _load_json_map(raw: str, vol_file: Path) -> dict[int, int]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"Could not parse volume map as JSON ({vol_file}): {exc}")
    if isinstance(data, dict):
        data = data.get("volumes", [])
    ch_vol: dict[int, int] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            vol = int(entry["volume"])
            start = int(entry["start"])
            end = int(entry["end"])
        except (KeyError, TypeError, ValueError):
            continue
        for ch in range(start, end + 1):
            ch_vol[ch] = vol
    return ch_vol


def _load_pipe_map(raw: str) -> dict[int, int]:
    ch_vol: dict[int, int] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        try:
            vol, start, end = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        for ch in range(start, end + 1):
            ch_vol[ch] = vol
    return ch_vol


# ---------------------------------------------------------------------------
# ComicInfo.xml
# ---------------------------------------------------------------------------


def read_comicinfo(path: Path) -> dict[str, str]:
    """Reads ComicInfo.xml out of a zip-based archive. Returns {} on any
    failure — a missing, malformed, or unreadable archive is never fatal, it
    just means the filename is all we have."""
    if path.suffix.lstrip(".").lower() not in ZIP_EXTS:
        return {}
    try:
        with zipfile.ZipFile(path) as zf:
            name = next(
                (n for n in zf.namelist() if Path(n).name.lower() == "comicinfo.xml"),
                None,
            )
            if name is None:
                return {}
            root = ET.fromstring(zf.read(name))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError, ValueError):
        return {}

    fields: dict[str, str] = {}
    for child in root:
        # Strip any XML namespace ComicRack-alike writers add.
        tag = child.tag.rpartition("}")[2]
        if child.text and child.text.strip():
            fields[tag.lower()] = child.text.strip()
    return fields


def make_augmenter(use_comicinfo: bool) -> Callable[[Path, ChapterInfo], None] | None:
    """Fills only the ChapterInfo fields the filename left blank.

    Same contract as jellyname's apply_probed_tags: the filename is
    authoritative, the embedded metadata only fills gaps.
    """
    if not use_comicinfo:
        return None

    def augment(filepath: Path, parsed: ChapterInfo) -> None:
        fields = read_comicinfo(filepath)
        if not fields:
            return

        # Kavita's HasComicInfoSpecial: a Format naming a special keyword makes
        # the file a special, but only if nothing numbered it first.
        fmt = fields.get("format", "").strip().lower()
        if fmt in SPECIAL_KEYWORDS and not parsed.has_number() and not parsed.is_special:
            parsed.is_special = True
            if not parsed.special_title:
                parsed.special_title = sanitize_for_path(fields.get("title", "")) or fields["format"]

        if parsed.is_special:
            if not parsed.special_title and fields.get("title"):
                parsed.special_title = sanitize_for_path(fields["title"])
            return

        if not parsed.volume and _is_number(fields.get("volume", "")):
            parsed.volume = fields["volume"].strip()
        if not parsed.chapter and _is_number(fields.get("number", "")):
            parsed.chapter = fields["number"].strip()

    return augment


def _is_number(value: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", value.strip()))


def comicinfo_series(path: Path) -> str:
    return sanitize_for_path(read_comicinfo(path).get("series", ""))


# ---------------------------------------------------------------------------
# discovery + rename
# ---------------------------------------------------------------------------


def discover_files(folder: Path, kind: str) -> list[Path]:
    exts = EXTS_FOR_KIND[kind]
    found: list[Path] = []
    for p in _candidate_paths(folder):
        if not p.is_file() or is_junk(p) or is_cover_image(p.name):
            continue
        if p.suffix.lstrip(".").lower() in exts:
            found.append(p)
    return sorted(found, key=lambda p: _natural_key(str(p)))


def _candidate_paths(folder: Path) -> Iterable[Path]:
    """The folder's own files, plus any already in a Specials/ subfolder — so a
    re-run renumbers existing specials instead of ignoring them."""
    yield from folder.iterdir()
    specials = folder / SPECIALS_DIR
    if specials.is_dir():
        yield from specials.iterdir()


def resolve_series(folder: Path, files: list[Path], explicit: str | None, source: str) -> str:
    if explicit:
        return sanitize_for_path(explicit)

    if source == "comicinfo":
        for f in files:
            name = comicinfo_series(f)
            if name:
                return name
        warn("No ComicInfo.xml series found — falling back to folder name.")
    elif source == "filename":
        for f in files:
            name = parse_stem(f.stem).series
            if name:
                return name
        warn("Could not parse a series name from any filename — falling back to folder name.")

    return sanitize_for_path(folder.name)


def do_rename(src: Path, target_dir: Path, new_name: str, base: Path, dry_run: bool) -> str:
    """Returns 'renamed', 'unchanged', or 'skipped'."""
    target = target_dir / new_name

    if src.parent == target_dir and src.name == new_name:
        info(f"Unchanged: {_rel(src, base)}")
        return "unchanged"

    if dry_run:
        console.print(f"  [cyan]{_rel(src, base)}[/cyan]")
        console.print(f"  [bold]→[/bold] [green]{_rel(target, base)}[/green]")
        console.print()
        return "renamed"

    # A case-only rename resolves to the same file on APFS/NTFS; that's a real
    # rename to perform, not a collision to refuse.
    if target.exists() and not _same_file(src, target):
        warn(f"Target already exists, skipping: {_rel(target, base)}")
        return "skipped"

    target_dir.mkdir(parents=True, exist_ok=True)
    src.rename(target)
    ok(f"{_rel(src, base)}  →  {_rel(target, base)}")
    return "renamed"


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def make_special_counter(files: list[Path]) -> Callable[[], int]:
    """Hands out SP numbers that don't collide with ones already in use."""
    used = {parse_stem(f.stem).special_index for f in files}
    used.discard(0)
    state = {"next": 1}

    def next_index() -> int:
        while state["next"] in used:
            state["next"] += 1
        used.add(state["next"])
        return state["next"]

    return next_index


def process_folder(folder: Path, args: argparse.ArgumentParser, volume_map: dict[int, int]) -> tuple[int, int, int]:
    files = discover_files(folder, args.type)
    if not files:
        warn(f"No {args.type} files found in: {folder}")
        return 0, 0, 0

    series = resolve_series(folder, files, args.series, args.series_from)
    info(f'{folder.name}: {len(files)} file(s), series "{series}"')
    console.print()

    augment = make_augmenter(not args.no_comicinfo)
    next_special = make_special_counter(files)

    renamed = unchanged = skipped = 0
    for f in files:
        parsed = process_file(f, series, args.type, volume_map, augment, next_special)
        if parsed is None:
            warn(f"Could not determine volume/chapter from: {_rel(f, folder)}  — skipping")
            skipped += 1
            continue
        target_dir, new_name = parsed
        result = do_rename(f, target_dir, new_name, folder, args.dry_run)
        if result == "renamed":
            renamed += 1
        elif result == "unchanged":
            unchanged += 1
        else:
            skipped += 1

    return renamed, unchanged, skipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kavitaname",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("type", choices=["manga", "manhwa", "book"], help="Content type")
    parser.add_argument("directory", nargs="?", default=".", help="Series folder to process (default: current directory)")
    parser.add_argument("-s", "--series", default=None, help="Series name override")
    parser.add_argument(
        "-S", "--series-from", choices=["folder", "comicinfo", "filename"], default="folder",
        help="Where to take the series name from when -s isn't given (default: folder)",
    )
    parser.add_argument("-m", "--volume-map", default=None, help="Chapter-to-volume map file (JSON or pipe-delimited)")
    parser.add_argument("-r", "--recursive", action="store_true", help="Treat each immediate subfolder as its own series")
    parser.add_argument("-C", "--no-comicinfo", action="store_true", help="Don't read ComicInfo.xml from inside archives")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview renames without making any changes")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.recursive and args.series:
        die("-s/--series can't be combined with -r/--recursive — each subfolder is its own series.")

    root = Path(args.directory).expanduser()
    if not root.is_dir():
        die(f"Directory not found: {root}")
    root = root.resolve()

    volume_map: dict[int, int] = {}
    if args.volume_map:
        vol_file = Path(args.volume_map).expanduser()
        if not vol_file.is_file():
            die(f"Volume map file not found: {vol_file}")
        volume_map = load_volume_map(vol_file)
        if not volume_map:
            warn(f"Volume map loaded but contained no usable entries: {vol_file}")
        else:
            info(f"Loaded {len(volume_map)} chapter-to-volume mappings from {vol_file}")

    if args.dry_run:
        info("Dry run — no files will be moved.")
    console.print()

    if args.recursive:
        folders = sorted(
            (p for p in root.iterdir() if p.is_dir() and p.name.lower() != SPECIALS_DIR.lower()),
            key=lambda p: _natural_key(p.name),
        )
        if not folders:
            die(f"No subfolders found in: {root}")
    else:
        folders = [root]

    renamed = unchanged = skipped = 0
    for folder in folders:
        r, u, s = process_folder(folder, args, volume_map)
        renamed += r
        unchanged += u
        skipped += s

    console.print()
    if args.dry_run:
        info(f"Dry run complete — {renamed} would be renamed, {unchanged} already correct, {skipped} skipped.")
    else:
        ok(f"Done — {renamed} renamed, {unchanged} already correct, {skipped} skipped.")


if __name__ == "__main__":
    main()
