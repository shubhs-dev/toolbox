"""Shared rich Console and styled log helpers used across all toolboxcli scripts."""

from __future__ import annotations

import sys
from typing import NoReturn

from rich.console import Console

console = Console()
_err_console = Console(stderr=True)


def info(msg: str) -> None:
    console.print(f"[cyan][INFO][/cyan] {msg}")


def ok(msg: str) -> None:
    console.print(f"[green][OK][/green] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow][WARN][/yellow] {msg}")


def error(msg: str) -> None:
    _err_console.print(f"[red][ERROR][/red] {msg}")


def die(msg: str, code: int = 1) -> NoReturn:
    error(msg)
    sys.exit(code)
