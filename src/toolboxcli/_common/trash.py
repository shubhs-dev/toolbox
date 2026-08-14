"""Cross-platform "move to trash/recycle bin" — never permanently delete user files."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path


def move_to_trash(path: str | Path) -> None:
    """Move ``path`` to the OS trash/recycle bin.

    Tries ``send2trash`` first (handles Windows/macOS/Linux natively). Falls back to
    OS-specific tools if send2trash isn't installed or fails, and raises RuntimeError
    if no trash mechanism is available at all — callers should never fall through to
    a plain delete silently.
    """
    p = Path(path)

    try:
        import send2trash

        send2trash.send2trash(str(p))
        return
    except ImportError:
        pass
    except Exception:
        pass  # fall through to OS-specific handling below

    system = platform.system()

    if system == "Windows":
        _trash_windows(p)
        return

    if system == "Linux":
        if shutil.which("gio"):
            subprocess.run(["gio", "trash", str(p)], check=True)
            return
        if shutil.which("trash-put"):
            subprocess.run(["trash-put", str(p)], check=True)
            return
        raise RuntimeError(
            f"no trash mechanism available for {p} (install send2trash, or gio/trash-put)"
        )

    if system == "Darwin":
        posix_path = str(p.resolve()).replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell app "Finder" to delete POSIX file "{posix_path}"'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"could not trash {p}: osascript failed: {result.stderr.strip()}"
            )
        return

    raise RuntimeError(f"no trash mechanism available for {p} on platform {system!r}")


def _trash_windows(p: Path) -> None:
    ps_script = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
        f"'{p.resolve()}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps_script],
        check=True,
        capture_output=True,
    )
