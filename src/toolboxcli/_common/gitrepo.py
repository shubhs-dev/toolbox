"""Git repo discovery and a thin subprocess wrapper, shared by any script that walks git history."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def discover_repos(root: Path) -> list[Path]:
    """Find all git repos under root. Does not descend into a found repo's .git dir,
    but does keep walking into its other subdirectories in case of nested/embedded repos.
    """
    repos: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        if ".git" in dirnames:
            repos.append(Path(dirpath))
            dirnames.remove(".git")
    return repos


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
