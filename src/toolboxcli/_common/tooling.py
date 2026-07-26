"""Check that a required external CLI tool is on PATH before shelling out to it."""

from __future__ import annotations

import shutil

from toolboxcli._common.console import die


def require_tool(name: str, install_hint: str | None = None) -> None:
    if shutil.which(name) is None:
        msg = f"'{name}' is not installed or not on PATH"
        if install_hint:
            msg += f" ({install_hint})"
        die(msg)
