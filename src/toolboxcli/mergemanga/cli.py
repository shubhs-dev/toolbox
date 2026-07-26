"""
mergemanga — Merge individual One Piece chapter CBZ files into volume CBZ files.

Reads "Chapter N.cbz" files from <source_dir>, groups them into volumes using
a built-in volume map (1-113, with official English titles), and writes each
volume as a Calibre-compatible CBZ with a generated ComicInfo.xml. CBZ files
are just zip archives, so this uses the stdlib zipfile module directly — no
external archiving tool required.

Output naming: "Eiichiro Oda - One Piece <NN> - <Title>.cbz"

Usage:
    mergemanga [source_dir] [output_dir]
    mergemanga ./chapters ./Volumes
    mergemanga --dry-run
"""

from __future__ import annotations

import argparse
import re
import tempfile
import zipfile
from importlib.resources import files as _resource_files
from pathlib import Path

from toolboxcli._common.console import console, die, info, ok, warn

AUTHOR = "Eiichiro Oda"
SERIES = "One Piece"
PUBLISHER = "VIZ Media"
GENRE = "Action, Adventure, Comedy, Fantasy, Shounen"
ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*]')


def load_volume_map() -> list[dict]:
    data_path = _resource_files("toolboxcli.mergemanga") / "data" / "volume_map.json"
    import json

    with data_path.open() as f:
        return json.load(f)


def sanitize_title(title: str) -> str:
    return ILLEGAL_CHARS_RE.sub("-", title)


def volume_output_name(vol_num: int, title: str) -> str:
    vol_padded = f"{vol_num:02d}" if vol_num < 100 else str(vol_num)
    return f"{AUTHOR} - {SERIES} {vol_padded} - {sanitize_title(title)}.cbz"


def build_comicinfo_xml(vol_num: int, title: str, ch_start: int, ch_end: int, total_volumes: int) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <Title>{title}</Title>
  <Series>{SERIES}</Series>
  <Number>{vol_num}</Number>
  <Count>{total_volumes}</Count>
  <Volume>{vol_num}</Volume>
  <Writer>{AUTHOR}</Writer>
  <Penciller>{AUTHOR}</Penciller>
  <Inker>{AUTHOR}</Inker>
  <Letterer>{AUTHOR}</Letterer>
  <CoverArtist>{AUTHOR}</CoverArtist>
  <Publisher>{PUBLISHER}</Publisher>
  <Genre>{GENRE}</Genre>
  <LanguageISO>en</LanguageISO>
  <Manga>Yes</Manga>
  <Summary>{SERIES} Vol. {vol_num} - {title} (Chapters {ch_start}-{ch_end})</Summary>
</ComicInfo>
"""


def build_volume(
    entry: dict, source_dir: Path, output_dir: Path, dry_run: bool, total_volumes: int
) -> str:
    """Build one volume CBZ. Returns 'built', 'skipped-exists', or 'skipped-empty'."""
    vol_num = entry["volume"]
    ch_start, ch_end = entry["start"], entry["end"]
    title = entry["title"]

    out_name = volume_output_name(vol_num, title)
    out_path = output_dir / out_name

    if out_path.exists():
        info(f"Already exists, skipping: {out_name}")
        return "skipped-exists"

    chapter_files: list[tuple[int, Path]] = []
    missing: list[int] = []
    for ch in range(ch_start, ch_end + 1):
        chfile = source_dir / f"Chapter {ch}.cbz"
        if chfile.is_file():
            chapter_files.append((ch, chfile))
        else:
            missing.append(ch)

    if not chapter_files:
        return "skipped-empty"

    if missing:
        warn(f"Volume {vol_num} ({title}): missing chapters: {missing}")

    if dry_run:
        info(f"[dry-run] Would build: {out_name}  (chapters {ch_start}-{ch_end})")
        return "built"

    info(f"Building: {out_name}  (chapters {ch_start}-{ch_end})")

    with tempfile.TemporaryDirectory() as tmpdir_str:
        staging = Path(tmpdir_str) / "volume"
        staging.mkdir()

        for ch_num, chfile in chapter_files:
            ch_dir = staging / f"ch_{ch_num:04d}"
            ch_dir.mkdir()
            try:
                with zipfile.ZipFile(chfile) as zf:
                    zf.extractall(ch_dir)
            except zipfile.BadZipFile:
                warn(f"Failed to extract: {chfile}")

        (staging / "ComicInfo.xml").write_text(
            build_comicinfo_xml(vol_num, title, ch_start, ch_end, total_volumes), encoding="utf-8"
        )

        volume_cbz = Path(tmpdir_str) / "volume.cbz"
        with zipfile.ZipFile(volume_cbz, "w", zipfile.ZIP_STORED) as zf:
            for member in sorted(staging.rglob("*")):
                if member.is_file():
                    zf.write(member, member.relative_to(staging))

        output_dir.mkdir(parents=True, exist_ok=True)
        volume_cbz.replace(out_path)

    ok(f"Done: {out_name}")
    return "built"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mergemanga",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source_dir", nargs="?", default=".",
        help='Directory containing "Chapter N.cbz" files (default: .)',
    )
    parser.add_argument(
        "output_dir", nargs="?", default="./Volumes",
        help="Directory to write volume CBZ files (default: ./Volumes)",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Preview which volumes would be built, without writing anything",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    output_dir = Path(args.output_dir)

    if not source_dir.is_dir():
        die(f"Source directory '{source_dir}' does not exist.")

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    volume_map = load_volume_map()

    built = 0
    skipped = 0

    total_volumes = len(volume_map)
    for entry in volume_map:
        result = build_volume(entry, source_dir, output_dir, args.dry_run, total_volumes)
        if result == "built":
            built += 1
        elif result == "skipped-exists":
            skipped += 1

    console.print()
    info(f"Finished! Built {built} volume(s), skipped {skipped} existing.")
    info(f"Output: {output_dir}/")


if __name__ == "__main__":
    main()
