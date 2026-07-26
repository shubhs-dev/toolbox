"""Cyberpunk 2077 mod-installation logic: archive detection, overwrite checks, install."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Literal

from toolboxcli._common.console import console, error, info, ok, warn
from toolboxcli._common.trash import move_to_trash

KNOWN_DIRS = {"archive", "bin", "engine", "r6", "red4ext", "mods", "tools"}


class InstallStatus(IntEnum):
    SUCCESS = 0
    FAILED = 1
    SKIPPED = 2


@dataclass
class ModRoot:
    kind: Literal["dirs", "loose_archive"]
    path: Path


def extract(archive: Path, dest: Path) -> bool:
    """Extract archive to dest using 7z. Returns True on success."""
    result = subprocess.run(
        ["7z", "x", f"-o{dest}", "-y", str(archive)],
        capture_output=True,
    )
    return result.returncode == 0


def _known_subdirs(directory: Path) -> list[str]:
    return [
        d.name for d in directory.iterdir()
        if d.is_dir() and d.name.lower() in KNOWN_DIRS
    ]


def find_mod_root(extract_dir: Path) -> ModRoot | None:
    """Locate the mod root inside extracted archive contents."""
    if _known_subdirs(extract_dir):
        return ModRoot("dirs", extract_dir)

    subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
    if len(subdirs) == 1:
        if _known_subdirs(subdirs[0]):
            return ModRoot("dirs", subdirs[0])
        inner_subdirs = [d for d in subdirs[0].iterdir() if d.is_dir()]
        if len(inner_subdirs) == 1 and _known_subdirs(inner_subdirs[0]):
            return ModRoot("dirs", inner_subdirs[0])

    # Loose .archive files within 2 levels deep
    for depth_glob in ("*.archive", "*/*.archive"):
        if next(extract_dir.glob(depth_glob), None) is not None:
            return ModRoot("loose_archive", extract_dir)

    return None


def find_overwrites(src_dir: Path, dest_dir: Path) -> list[str]:
    """Return relative paths under src_dir that already exist under dest_dir."""
    if not dest_dir.is_dir():
        return []
    overwrites = []
    for f in src_dir.rglob("*"):
        if f.is_file():
            rel = f.relative_to(src_dir)
            if (dest_dir / rel).is_file():
                overwrites.append(str(rel))
    return overwrites


def _loose_archive_files(loose_dir: Path) -> list[Path]:
    return sorted(loose_dir.rglob("*.archive"))


def preview_contents(mod_root: ModRoot) -> None:
    if mod_root.kind == "loose_archive":
        files = _loose_archive_files(mod_root.path)
        info(f"Mod contains {len(files)} .archive file(s) -> archive/pc/mod/")
        for f in files:
            console.print(f"  {f.name}")
    else:
        info("Mod contents:")
        for name in _known_subdirs(mod_root.path):
            console.print(f"    [green]{name}/[/green]")
        for item in sorted(mod_root.path.iterdir()):
            if item.is_file():
                console.print(f"    {item.name}")


def compute_overwrites(mod_root: ModRoot, game_dir: Path) -> list[str]:
    overwrite_list: list[str] = []
    if mod_root.kind == "loose_archive":
        target = game_dir / "archive" / "pc" / "mod"
        if target.is_dir():
            for f in _loose_archive_files(mod_root.path):
                if (target / f.name).is_file():
                    overwrite_list.append(f"archive/pc/mod/{f.name}")
    else:
        for name in _known_subdirs(mod_root.path):
            hits = find_overwrites(mod_root.path / name, game_dir / name)
            overwrite_list.extend(f"{name}/{h}" for h in hits)
        for item in sorted(mod_root.path.iterdir()):
            if item.is_file() and (game_dir / item.name).is_file():
                overwrite_list.append(item.name)
    return overwrite_list


def perform_install(mod_root: ModRoot, game_dir: Path) -> tuple[int, int]:
    """Copy mod contents into game_dir. Returns (dirs_or_files_installed, loose_files)."""
    if mod_root.kind == "loose_archive":
        target = game_dir / "archive" / "pc" / "mod"
        target.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in _loose_archive_files(mod_root.path):
            shutil.copy2(f, target / f.name)
            console.print(f"    {f.name}")
            count += 1
        ok(f"Installed {count} .archive file(s)")
        return count, 0

    installed = 0
    for name in _known_subdirs(mod_root.path):
        shutil.copytree(mod_root.path / name, game_dir / name, dirs_exist_ok=True)
        ok(f"  Copied [green]{name}/[/green] -> game directory")
        installed += 1

    loose_files = 0
    for item in sorted(mod_root.path.iterdir()):
        if item.is_file():
            shutil.copy2(item, game_dir / item.name)
            ok(f"  Copied [green]{item.name}[/green] -> game directory")
            loose_files += 1

    return installed, loose_files


def install_mod(
    archive: Path,
    idx: int,
    total: int,
    game_dir: Path,
    yes: bool,
    dry_run: bool,
    confirm_fn,
) -> InstallStatus:
    """Extract, preview, confirm, and install a single mod archive."""
    filename = archive.name
    console.print(f"\n[cyan]{'━' * 42}[/cyan]")
    console.print(f"[cyan] [{idx}/{total}][/cyan] {filename}")
    console.print(f"[cyan]{'━' * 42}[/cyan]")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)

        if not extract(archive, tmp_dir):
            error(f"Failed to extract {filename}")
            return InstallStatus.FAILED

        mod_root = find_mod_root(tmp_dir)
        if mod_root is None:
            warn(f"Could not detect mod structure in {filename}")
            warn("Contents:")
            for item in sorted(tmp_dir.iterdir()):
                console.print(f"    {item.name}")
            console.print()
            return InstallStatus.FAILED

        preview_contents(mod_root)

        if not yes and not dry_run:
            if not confirm_fn(f"Install {filename}?"):
                info(f"Skipped {filename}")
                return InstallStatus.SKIPPED

        overwrite_list = compute_overwrites(mod_root, game_dir)
        if overwrite_list:
            warn("The following existing files will be OVERWRITTEN:")
            for line in overwrite_list:
                console.print(f"[yellow]  {line}[/yellow]")
            if not yes and not dry_run:
                if not confirm_fn("Continue and overwrite these files?"):
                    info(f"Skipped {filename}")
                    return InstallStatus.SKIPPED

        if dry_run:
            info(f"[dry-run] Would install {filename}")
            return InstallStatus.SUCCESS

        installed, loose_files = perform_install(mod_root, game_dir)
        if installed == 0 and loose_files == 0 and mod_root.kind == "dirs":
            warn(f"No recognized mod directories found in {filename}")
            warn("Contents of detected root:")
            for item in sorted(mod_root.path.iterdir()):
                console.print(f"    {item.name}")
            return InstallStatus.FAILED

        ok(f"Installed {filename} ({installed} dir(s)/file(s), {loose_files} loose file(s))")

    try:
        move_to_trash(str(archive))
        ok(f"Moved {filename} to Recycle Bin")
    except Exception:
        warn(f"Could not move {filename} to Recycle Bin (deleting instead)")
        archive.unlink(missing_ok=True)

    console.print()
    return InstallStatus.SUCCESS
