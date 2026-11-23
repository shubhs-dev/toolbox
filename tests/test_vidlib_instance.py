"""One vidlib per library (the lock), and several side by side (the port fallback)."""

from __future__ import annotations

import http.client
import os
import socket

import pytest

from toolboxcli.vidlib.db import LibraryDB
from toolboxcli.vidlib.downloads import DownloadManager
from toolboxcli.vidlib.instance import LOCK_NAME, LibraryBusy, LibraryLock
from toolboxcli.vidlib.server import bind_server, port_busy


def test_a_library_can_be_locked_once_at_a_time(tmp_path):
    with LibraryLock(tmp_path) as first:
        with pytest.raises(LibraryBusy, match=f"pid {os.getpid()}"):
            LibraryLock(tmp_path).acquire()
        first.note("pid 1, page at http://127.0.0.1:8643/")
        with pytest.raises(LibraryBusy, match="page at http://127.0.0.1:8643/"):
            LibraryLock(tmp_path).acquire()
    with LibraryLock(tmp_path):              # released on exit
        pass
    assert (tmp_path / LOCK_NAME).is_file()  # a leftover file never blocks: the OS lock does


def test_the_lock_never_creates_a_missing_library(tmp_path):
    with pytest.raises(OSError):
        LibraryLock(tmp_path / "unplugged").acquire()
    assert not (tmp_path / "unplugged").exists()


def test_a_second_vidlib_on_the_same_library_stops_but_queries_still_run(tmp_path, run_cli):
    root = tmp_path / "lib"
    root.mkdir()
    LibraryDB.open(root).close()
    with LibraryLock(root):
        busy = run_cli("vidlib", [str(root), "-O"])
        assert busy.returncode != 0
        assert "already running on this library" in busy.stdout + busy.stderr
        listing = run_cli("vidlib", [str(root), "-l"])
        assert listing.returncode == 0, listing.stderr


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    db = LibraryDB.open(root, shared=True)
    yield DownloadManager(root, db, workers=1)
    db.close()


@pytest.fixture
def taken_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    yield sock.getsockname()[1]
    sock.close()


def test_a_busy_port_moves_the_page_to_the_next_free_one(manager, taken_port):
    server = bind_server(manager, taken_port)
    try:
        port = server.server_address[1]
        assert port > taken_port
        server.start()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")                   # the Host check follows the real port
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        server.stop()


def test_no_free_port_in_range_is_an_error(manager, taken_port):
    with pytest.raises(OSError) as raised:
        bind_server(manager, taken_port, tries=1)
    assert port_busy(raised.value)


def test_a_filesystem_without_locks_is_not_a_busy_library(tmp_path, monkeypatch):
    import errno

    from toolboxcli.vidlib import instance

    def unsupported(fd: int) -> None:
        raise OSError(errno.ENOTSUP, "locks not supported")

    monkeypatch.setattr(instance, "_lock", unsupported)
    with pytest.raises(OSError) as raised:
        LibraryLock(tmp_path).acquire()
    assert not isinstance(raised.value, LibraryBusy)
