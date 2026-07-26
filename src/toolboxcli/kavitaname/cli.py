"""
kavitaname — Rename chapter CBZ/CBR files for Kavita server compatibility.

Scans the current folder for "Chapter N.cbz" / "Chapter N.cbr" (and "Prologue N")
files. For manga, optionally prompts for a volume-map data file (pipe-delimited:
vol_number|first_chapter|last_chapter|Title, '#' comments and blank lines ignored,
same format as mergemanga's data file) -- press Enter to skip.

Output format:
    manga (with map):  {Series} c{ch:03d} (v{vol:02d}).cbz   e.g. "One Piece c001 (v01).cbz"
    manga (no map):     {Series} c{ch:03d}.cbz
    manhwa:             {Series} c{ch:03d}.cbz               e.g. "Solo Leveling c001.cbz"
    prologue:           {Series} c000.N.cbz  (N = prologue number)

Usage:
    kavitaname <type> [series-name]
    kavitaname manga "One Piece"
    kavitaname manhwa
    kavitaname -n manga "Berserk"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from toolboxcli._common.console import console, die, info, ok, warn

CHAPTER_PREFIX_RE = re.compile(r"^chapter\s*", re.IGNORECASE)
PROLOGUE_PREFIX_RE = re.compile(r"^prologue", re.IGNORECASE)
CHAPTER_NUM_RE = re.compile(r"^\d+(\.\d+)?")
TRAILING_INT_RE = re.compile(r"\d+$")


def _natural_key(name: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def load_volume_map(vol_file: Path) -> dict[int, int]:
    ch_vol: dict[int, int] = {}
    for line in vol_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        vol_num = parts[0].replace("\r", "").strip()
        ch_start = parts[1].replace("\r", "").strip()
        ch_end = parts[2].replace("\r", "").strip()
        for ch in range(int(ch_start), int(ch_end) + 1):
            ch_vol[ch] = int(vol_num)
    return ch_vol


def discover_files(cwd: Path) -> list[Path]:
    files = [
        p for p in cwd.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".cbz", ".cbr")
        and (p.name.lower().startswith("chapter ") or p.name.lower().startswith("prologue "))
    ]
    return sorted(files, key=lambda p: _natural_key(p.name))


def parse_chapter(stem: str) -> tuple[str, bool] | None:
    """Returns (ch_num_str, is_prologue), or None if unparseable. ch_num_str may be decimal."""
    if PROLOGUE_PREFIX_RE.match(stem):
        m = TRAILING_INT_RE.search(stem)
        if not m:
            return None
        return f"0.{m.group(0)}", True

    rest = CHAPTER_PREFIX_RE.sub("", stem)
    m = CHAPTER_NUM_RE.match(rest)
    if not m:
        return None
    return m.group(0), False


def build_new_name(series: str, ch_num: str, ext: str, kind: str, ch_vol: dict[int, int], is_prologue: bool) -> str | None:
    """Returns the new filename, or None if a manga chapter isn't in the volume map."""
    ch_int = int(ch_num.split(".", 1)[0])
    ch_padded = f"{ch_int:03d}"
    if "." in ch_num:
        ch_padded += "." + ch_num.split(".", 1)[1]

    if kind == "manga" and not is_prologue and ch_vol:
        vol_num = ch_vol.get(ch_int)
        if vol_num is None:
            return None
        return f"{series} c{ch_padded} (v{vol_num:02d}).{ext}"

    return f"{series} c{ch_padded}.{ext}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kavitaname",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("type", choices=["manga", "manhwa"], help="Content type")
    parser.add_argument("series_name", nargs="?", default=None, help="Series name override (default: current folder name)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview renames without making any changes")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    cwd = Path.cwd()
    series = args.series_name
    if not series:
        series = cwd.name
        info(f"Series name not provided — using folder name: {series}")

    ch_vol: dict[int, int] = {}
    if args.type == "manga":
        raw = console.input("Volume map file (Enter to skip): ").strip()
        if raw:
            vol_file = Path(raw).expanduser()
            if not vol_file.is_file():
                die(f"Volume map file not found: {vol_file}")
            info(f"Loading volume map: {vol_file}")
            ch_vol = load_volume_map(vol_file)
            info(f"Loaded {len(ch_vol)} chapter-to-volume mappings.")
        else:
            info("No volume map provided — chapters will be named without volume info.")

    files = discover_files(cwd)
    if not files:
        die(f"No 'Chapter *.cbz' / 'Chapter *.cbr' / 'Prologue *.cbz' files found in: {cwd}")

    info(f'Found {len(files)} file(s). Series: "{series}"  Type: {args.type}')
    if args.dry_run:
        info("(dry-run — no files will be renamed)")
    console.print()

    renamed = 0
    skipped = 0

    for f in files:
        ext = f.suffix.lstrip(".")
        stem = f.stem

        parsed = parse_chapter(stem)
        if parsed is None:
            label = "prologue" if PROLOGUE_PREFIX_RE.match(stem) else "chapter"
            warn(f"Cannot parse {label} number from: {f.name}  — skipping")
            skipped += 1
            continue
        ch_num, is_prologue = parsed

        new_name = build_new_name(series, ch_num, ext, args.type, ch_vol, is_prologue)
        if new_name is None:
            warn(f"Chapter {ch_num} not in volume map — skipping: {f.name}")
            skipped += 1
            continue

        if f.name == new_name:
            info(f"Unchanged: {f.name}")
            skipped += 1
            continue

        if args.dry_run:
            info(f"{f.name}  →  {new_name}")
            continue

        new_path = cwd / new_name
        if new_path.exists():
            warn(f"Target already exists, skipping: {new_name}")
            skipped += 1
            continue

        f.rename(new_path)
        ok(f"{f.name}  →  {new_name}")
        renamed += 1

    console.print()
    if args.dry_run:
        info(f"Dry-run complete — {len(files)} file(s) previewed.")
    else:
        info(f"Done. Renamed: {renamed}  Skipped: {skipped}")


if __name__ == "__main__":
    main()
