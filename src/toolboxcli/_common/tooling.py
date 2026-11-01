"""External CLI tools: check one is on PATH before shelling out to it, and launch it
detached from the terminal."""

from __future__ import annotations

import os
import shutil
import subprocess

from toolboxcli._common.console import die


def require_tool(name: str, install_hint: str | None = None) -> None:
    if shutil.which(name) is None:
        msg = f"'{name}' is not installed or not on PATH"
        if install_hint:
            msg += f" ({install_hint})"
        die(msg)


def detached_kwargs() -> dict:
    """Popen/run kwargs that cut a child process off from the terminal entirely, while
    leaving any stdout/stderr pipes the caller sets up untouched.

    Piping a child's output isn't enough to silence it: a library can still write
    straight to the terminal device. HandBrake's bundled libdvdread does exactly that —
    it probes every input as a DVD and prints "libdvdread: Can't open file
    VIDEO_TS.IFO." past the pipes, once per file. With no stdin, no controlling
    terminal (POSIX: a new session) and no console (Windows: CREATE_NO_WINDOW), there's
    nowhere left for it to write.

    The flip side: the child no longer gets the terminal's Ctrl+C, so a caller that
    waits on a long-running child must terminate it itself on KeyboardInterrupt.
    `subprocess.run` already kills its child when interrupted.
    """
    if os.name == "nt":
        return {"stdin": subprocess.DEVNULL, "creationflags": subprocess.CREATE_NO_WINDOW}
    return {"stdin": subprocess.DEVNULL, "start_new_session": True}
