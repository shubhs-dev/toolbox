"""Safe output-file handling for scripts that turn one input file into one new output file."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from toolboxcli._common.console import die, info
from toolboxcli._common.trash import move_to_trash


def is_same_file(a: Path, b: Path) -> bool:
    """True if a and b are the same file however they're spelled — relative vs absolute,
    symlinks, hard links, or a different case on a case-insensitive filesystem (macOS,
    Windows). Comparing the Path objects directly misses all of these."""
    try:
        if a.resolve() == b.resolve():
            return True
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def check_output_path(input_path: Path, output_path: Path, force: bool) -> None:
    """Die unless output_path is safe to write: never the input file, its directory must
    exist, and an existing file is only allowed with force (it gets trashed on success)."""
    if is_same_file(input_path, output_path):
        die("output path must differ from input path")
    if not output_path.parent.is_dir():
        die(f"output directory does not exist: {output_path.parent}")
    if output_path.exists() and not force:
        die(f"{output_path} already exists — use -f/--force to replace it "
            "(the old file is moved to the Trash)")


def _new_file_mode() -> int:
    """The permissions a plain `open()` would have given a new file: 0666 masked by the
    process umask. `tempfile.mkstemp` deliberately creates its file 0600, which is right
    for a temp file and wrong for one that goes on to replace the output — a tool's output
    should look like any other file the user created. There's no way to read the umask
    without setting it, so it's set and immediately restored."""
    umask = os.umask(0o022)
    os.umask(umask)
    return 0o666 & ~umask


@contextmanager
def atomic_output(output_path: Path, force: bool) -> Iterator[Path]:
    """Yield a temp path beside output_path for the caller to write to. If the block
    finishes without raising, an existing output (allowed only with force) is moved to the
    Trash and the temp file takes its place; otherwise the temp file is removed and any
    existing output is left untouched. Call check_output_path() first.

    The temp file keeps output_path's extension so ffmpeg still picks the right muxer.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=output_path.suffix, dir=output_path.parent
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    os.chmod(tmp_path, _new_file_mode())
    try:
        yield tmp_path

        if output_path.exists():
            if not force:
                die(f"{output_path} appeared while working — not overwriting it")
            try:
                move_to_trash(output_path)
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                die(f"could not move the existing {output_path} to the Trash, left it in place: {e}")
            info(f"Moved the old {output_path.name} to the Trash")

        os.replace(tmp_path, output_path)
    finally:
        tmp_path.unlink(missing_ok=True)
