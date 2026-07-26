"""
jellyname — Rename and organize media files for Jellyfin compatibility.

Scans [directory] (top level only) for video files, parses each filename to
extract title, year, season/episode, and resolution, then renames and moves
each file into a Jellyfin-compatible folder structure inside <directory>.

Output structure:
    Movies:   Movie Name (Year)/Movie Name (Year) - 1080p.mkv
    TV/Anime: Show Name/Season 01/Show Name S01E01 - 720p.mkv

Resolution is preserved as a Jellyfin version-label suffix ( - 1080p).
Junk tags (BluRay, x264, HEVC, WEB-DL, etc.) are stripped from filenames.
Characters illegal in Jellyfin paths (< > : " / \\ | ? *) are removed.

If no directory is given, the current working directory is used.

Usage:
    jellyname [options] [directory]
    jellyname
    jellyname ~/Downloads/movies
    jellyname -n /mnt/media/shows
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from toolboxcli._common.console import console, die, info, ok, warn

VIDEO_EXTS = {"mkv", "mp4", "avi", "mov", "m4v", "ts", "webm"}

JUNK_RE = re.compile(
    r"\s*(2160p|1080[pi]|720[pi]|480[pi]|4[Kk]|"
    r"BluRay|Blu-Ray|Blu Ray|BDRip|BRRip|"
    r"WEB-DL|WEBRip|WEB DL|WEB|HDRip|DVDRip|HDTV|AMZN|HULU|DSNP|"
    r"x264|x265|H 264|H 265|HEVC|AVC|XviD|DivX|"
    r"AAC|AC3|DTS|FLAC|TrueHD|Atmos|DDP5 1|DD5 1|EAC3|"
    r"HDR10\+|HDR10|Dolby Vision|HDR|DV|SDR|"
    r"REMUX|PROPER|REPACK|EXTENDED|THEATRICAL|REMASTERED|IMAX|"
    r"10bit|10 bit|8bit|8 bit|Hi10P)\s*",
    re.IGNORECASE,
)

EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})|(\d{1,2})x(\d{1,2})", re.IGNORECASE)
EP_DETECT_RE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,2}|\d{1,2}x\d{1,2}", re.IGNORECASE)
BEFORE_SXXEXX_RE = re.compile(r"\s*[Ss]\d{1,2}[Ee]\d{1,2}.*")
BEFORE_NXN_RE = re.compile(r"\s*\d{1,2}[xX]\d{1,2}.*")
AFTER_SXXEXX_RE = re.compile(r".*[Ss]\d{1,2}[Ee]\d{1,2}\s*")
AFTER_NXN_RE = re.compile(r".*\d{1,2}[xX]\d{1,2}\s*")
RES_MID_RE = re.compile(r"\s*(2160[pi]|1080[pi]|720[pi]|480[pi]|4[Kk])\s.*")
RES_END_RE = re.compile(r"\s*(2160[pi]|1080[pi]|720[pi]|480[pi]|4[Kk])$")
TRAILING_DASH_RE = re.compile(r"\s*-\s*$")
LEADING_DASH_RE = re.compile(r"^\s*-\s*")
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
FOUR_K_RE = re.compile(r"(^|[^0-9a-zA-Z])4[Kk]($|[^0-9a-zA-Z])")
GENERIC_RES_RE = re.compile(r"(?:^|[^a-zA-Z])(2160[pi]|1080[pi]|720[pi]|480[pi])(?:$|[^a-zA-Z])", re.IGNORECASE)
ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*]')


def normalize_name(name: str) -> str:
    name = re.sub(r"[._]", " ", name)
    name = re.sub(r" +", " ", name).strip()
    return name


def strip_junk(name: str) -> str:
    prev = None
    while name != prev:
        prev = name
        name = JUNK_RE.sub(" ", name)
        name = re.sub(r" +", " ", name).strip()
    return name


def extract_resolution(raw: str) -> str:
    if FOUR_K_RE.search(raw):
        return "2160p"
    m = GENERIC_RES_RE.search(raw)
    return m.group(1).lower() if m else ""


def sanitize_for_path(name: str) -> str:
    name = ILLEGAL_CHARS_RE.sub("", name)
    return name.strip(". ")


def parse_tv(stem: str) -> tuple[str, str, str, str] | None:
    """Returns (show_name, season_num, episode_num, episode_title), or None if unparseable."""
    norm = normalize_name(stem)

    m = EP_RE.search(norm)
    if not m:
        return None

    if m.group(1) is not None:
        season_num, episode_num = m.group(1), m.group(2)
    else:
        season_num, episode_num = m.group(3), m.group(4)

    season_num = f"{int(season_num):02d}"
    episode_num = f"{int(episode_num):02d}"

    before_ep = BEFORE_SXXEXX_RE.sub("", norm)
    if before_ep == norm:
        before_ep = BEFORE_NXN_RE.sub("", norm)
    before_ep = strip_junk(before_ep)
    before_ep = sanitize_for_path(before_ep)
    before_ep = TRAILING_DASH_RE.sub("", before_ep).rstrip()
    show_name = before_ep

    after_ep = AFTER_SXXEXX_RE.sub("", norm)
    if after_ep == norm:
        after_ep = AFTER_NXN_RE.sub("", norm)
    after_ep = RES_MID_RE.sub("", after_ep)
    after_ep = RES_END_RE.sub("", after_ep)
    after_ep = strip_junk(after_ep)
    after_ep = sanitize_for_path(after_ep)
    after_ep = LEADING_DASH_RE.sub("", after_ep)
    after_ep = TRAILING_DASH_RE.sub("", after_ep).rstrip()
    episode_title = after_ep

    return show_name, season_num, episode_num, episode_title


def parse_movie(stem: str) -> tuple[str, str]:
    """Returns (movie_title, movie_year) — movie_year may be ""."""
    norm = normalize_name(stem)

    m = YEAR_RE.search(norm)
    year = m.group(1) if m else ""

    if year:
        before_year = re.sub(r"\s*" + re.escape(year) + r".*", "", norm)
        if not before_year:
            title = re.sub(r"^\s*" + re.escape(year) + r"\s*", "", norm)
        else:
            title = before_year
    else:
        title = norm

    title = strip_junk(title)
    title = sanitize_for_path(title)
    title = TRAILING_DASH_RE.sub("", title).rstrip()
    return title, year


def process_file(filepath: Path, src_dir: Path) -> tuple[Path, str] | None:
    """Returns (target_dir, new_filename), or None if the file should be skipped."""
    filename = filepath.name
    ext = filepath.suffix.lstrip(".").lower()
    stem = filepath.stem

    resolution = extract_resolution(stem)
    ver_suffix = f" - {resolution}" if resolution else ""

    if EP_DETECT_RE.search(stem):
        parsed = parse_tv(stem)
        if parsed is None:
            warn(f"Could not parse episode info from: {filename}")
            return None
        show_name, season_num, episode_num, episode_title = parsed
        if not show_name:
            warn(f"Could not determine show name from: {filename}")
            return None
        season_folder = f"Season {season_num}"
        ep_tag = f"S{season_num}E{episode_num}"
        ep_title_part = f" {episode_title}" if episode_title else ""
        new_filename = f"{show_name} {ep_tag}{ep_title_part}{ver_suffix}.{ext}"
        target_dir = src_dir / show_name / season_folder
        return target_dir, new_filename
    else:
        title, year = parse_movie(stem)
        if not title:
            warn(f"Could not determine movie title from: {filename}")
            return None
        folder_name = f"{title} ({year})" if year else title
        new_filename = f"{folder_name}{ver_suffix}.{ext}"
        target_dir = src_dir / folder_name
        return target_dir, new_filename


def do_rename(src_file: Path, target_dir: Path, new_filename: str, src_dir: Path, dry_run: bool) -> str:
    """Returns 'renamed' or 'skipped'."""
    target_path = target_dir / new_filename

    if target_path.exists() and src_file.resolve() == target_path.resolve():
        info(f"Already correct: {new_filename}")
        return "skipped"

    if dry_run:
        try:
            rel_target = target_dir.relative_to(src_dir)
            target_display = f"{rel_target}/{new_filename}"
        except ValueError:
            target_display = f"{target_dir}/{new_filename}"
        console.print(f"  [cyan]{src_file.name}[/cyan]")
        console.print(f"  [bold]→[/bold] [green]{target_display}[/green]")
        console.print()
        return "renamed"

    if target_path.exists():
        warn(f"Skipping (destination exists): {target_path}")
        return "skipped"

    target_dir.mkdir(parents=True, exist_ok=True)
    src_file.rename(target_path)
    ok(new_filename)
    return "renamed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jellyname",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("directory", nargs="?", default=".", help="Directory to scan (default: current directory)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview what would be renamed without moving any files")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    src_dir = Path(args.directory)
    if not src_dir.is_dir():
        die(f"Directory not found: {src_dir}")
    src_dir = src_dir.resolve()

    if args.dry_run:
        info("Dry run — no files will be moved.")
        console.print()

    info(f"Scanning: {src_dir}")

    files = sorted(
        p for p in src_dir.iterdir()
        if p.is_file() and p.suffix.lstrip(".").lower() in VIDEO_EXTS
    )

    if not files:
        warn(f"No video files found in {src_dir}")
        return

    info(f"Found {len(files)} video file(s).")
    if args.dry_run:
        console.print()

    renamed = 0
    skipped = 0

    for filepath in files:
        parsed = process_file(filepath, src_dir)
        if parsed is None:
            skipped += 1
            continue
        target_dir, new_filename = parsed
        result = do_rename(filepath, target_dir, new_filename, src_dir, args.dry_run)
        if result == "renamed":
            renamed += 1
        else:
            skipped += 1

    console.print()
    if args.dry_run:
        info(f"Dry run complete — {renamed} file(s) would be renamed, {skipped} skipped.")
    else:
        ok(f"Done — {renamed} renamed, {skipped} skipped.")


if __name__ == "__main__":
    main()
