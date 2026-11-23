"""External CLI tools: check one is on PATH before shelling out to it, and launch it
detached from the terminal."""

from __future__ import annotations

import os
import shutil
import signal
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


def terminate_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate a child started with `detached_kwargs()` *and* everything it started.

    `proc.terminate()` stops only the child itself. A tool that hands its work to another
    program — yt-dlp running aria2c — leaves that program running, still writing into the
    pipe the caller reads, so the caller sees the job carry on as if nothing happened.
    POSIX: the child leads its own session (`start_new_session`), so signal its process
    group. Windows: `taskkill /T` walks the tree from the child's pid.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, check=False, **detached_kwargs())
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()   # the group is already gone (or never was): the child alone
