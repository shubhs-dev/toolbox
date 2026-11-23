"""One vidlib at a time per library.

Two processes on one library would run two pipelines over the same root files (encoding,
moving and trashing them under each other) and two download queues over one table. The web
page's port used to be the only guard, and only by accident; now that a busy port just moves
the page to the next one, the library itself carries the lock.

The lock is an OS file lock on ``.vidlib.lock`` in the library root — ``flock`` on POSIX,
``msvcrt.locking`` on Windows — so it's released whenever the process ends, crash included,
and a stale lock file never blocks anything. Byte 0 is the locked byte; what follows it is
a note for whoever finds the library busy (the holder's pid, and its page once it's up).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType

LOCK_NAME = ".vidlib.lock"


class LibraryBusy(Exception):
    """Another process holds the library's lock. The message is its note, if it left one."""


class LibraryLock:
    def __init__(self, root: Path) -> None:
        self.path = root / LOCK_NAME
        self._fd: int | None = None

    def acquire(self) -> LibraryLock:
        # O_CREAT makes only the file itself: if the disk was unplugged the root is gone and
        # this fails, rather than creating a folder where the disk is mounted.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            raise LibraryBusy(read_note(self.path)) from None
        self._fd = fd
        self.note(f"pid {os.getpid()}")
        return self

    def note(self, text: str) -> None:
        """Replace the note after the locked byte."""
        if self._fd is None:
            return
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, b"\n" + text.encode() + b"\n")
        os.ftruncate(self._fd, 2 + len(text.encode()))

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> LibraryLock:
        return self.acquire()

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.release()


def read_note(path: Path) -> str:
    """The holder's note — read past byte 0, which Windows won't let anyone else read."""
    try:
        with open(path, "rb") as f:
            f.seek(1)
            return f.read(500).decode(errors="replace").strip()
    except OSError:
        return ""


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
