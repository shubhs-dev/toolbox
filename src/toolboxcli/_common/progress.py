"""Shared rich Progress factories: an indeterminate spinner and a determinate bar."""

from __future__ import annotations

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from toolboxcli._common.console import console as _default_console


def spinner(console: Console | None = None, transient: bool = False) -> Progress:
    """Indeterminate spinner + message, for scans/searches with no known total."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console or _default_console,
        transient=transient,
    )


def bar(console: Console | None = None) -> Progress:
    """Determinate progress bar with percentage and elapsed time, for known-length work."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console or _default_console,
    )
