"""The pipeline as the web page sees it: PipelineStatus, pausing and stopping, the unplugged-
disk guard, and a download following its video once the pipeline has filed it."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest
from conftest import needs

from toolboxcli._common import handbrake
from toolboxcli.vidlib import cli as vidlib
from toolboxcli.vidlib.db import Download, LibraryDB, Video
from toolboxcli.vidlib.downloads import DownloadManager
from toolboxcli.vidlib.server import VidlibServer
from toolboxcli.vidlib.status import KeepAwake, PipelineStatus

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake binaries are POSIX scripts")


# ── PipelineStatus ─────────────────────────────────────────────────────


def test_a_batch_is_reported_file_by_file():
    changes = []
    status = PipelineStatus(on_change=lambda: changes.append(1))
    status.begin_batch(["a.mp4", "b.mp4"])
    status.begin_file("a.mp4")
    status.encoding("hw-1080", "cpu")
    status.current["started"] -= 10                      # ten seconds in…
    status.progress(25)                                   # …a quarter done
    snap = status.snapshot()
    assert snap["state"] == "encoding" and snap["waiting"] == ["b.mp4"]
    assert snap["current"]["percent"] == 25 and snap["current"]["eta"] == 30
    status.end_file({"file": "a.mp4", "status": "sorted", "path": "C/a [720p].mp4"})
    status.end_batch()
    snap = status.snapshot()
    assert snap["current"] is None and snap["batch_done"] == 1
    assert snap["recent"][0]["path"] == "C/a [720p].mp4"
    assert changes


def test_resume_cuts_the_wait_short_and_stop_cancels():
    status = PipelineStatus()
    status.stop_now()
    assert status.paused and status.cancel.is_set()
    threading.Timer(0.2, status.resume).start()
    started = time.monotonic()
    status.sleep(10)
    assert time.monotonic() - started < 5
    assert not status.paused and not status.cancel.is_set()


def test_keep_awake_is_a_no_op_off_macos():
    keep = KeepAwake()
    keep.available = False
    keep.set(True)
    assert keep._proc is None


# ── the server's pipeline endpoints ────────────────────────────────────


@pytest.fixture
def server(tmp_path):
    started = []

    def start(pipeline=None):
        db = LibraryDB.open(tmp_path, shared=True)
        manager = DownloadManager(tmp_path, db)
        srv = VidlibServer(manager, port=0, pipeline=pipeline)
        if pipeline is not None:
            pipeline.on_change = manager.notify
        manager.start()
        srv.start()
        started.append((srv, manager, db))
        return srv

    yield start
    for srv, manager, db in started:
        srv.stop()
        manager.shutdown()
        db.close()


def _call(srv, method, path, body=None):
    import http.client
    import json
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    headers = {"Host": f"127.0.0.1:{srv.server_address[1]}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    out = res.status, json.loads(res.read())
    conn.close()
    return out


def test_pause_stop_and_resume_over_http(server):
    status = PipelineStatus(settings={"interval": 30})
    srv = server(status)
    assert _call(srv, "GET", "/api/state")[1]["pipeline"]["state"] == "starting"
    assert _call(srv, "POST", "/api/pipeline/pause", {})[0] == 200
    assert status.paused and not status.cancel.is_set()
    _call(srv, "POST", "/api/pipeline/stop", {})
    assert status.cancel.is_set()
    _call(srv, "POST", "/api/pipeline/resume", {})
    state = _call(srv, "GET", "/api/state")[1]
    assert state["pipeline"]["paused"] is False
    assert state["pipeline"]["settings"] == {"interval": 30}


def test_download_only_has_no_pipeline(server):
    srv = server(None)
    assert _call(srv, "GET", "/api/state")[1]["pipeline"] is None
    assert _call(srv, "POST", "/api/pipeline/pause", {})[0] == 409


def test_an_imported_download_follows_its_video(tmp_path):
    with LibraryDB.open(tmp_path, shared=True) as db:
        manager = DownloadManager(tmp_path, db)
        db.save_download(Download("https://x/a", "A - B - C", status="imported",
                                  library_path="A - B - C.mp4"))
        manager.start()
        manager.video_settled("A - B - C.mp4", "C/A - B - C [720p].mp4")
        manager.video_settled("Other.mp4", "C/Other.mp4")
        assert db.downloads()[0].library_path == "C/A - B - C [720p].mp4"
        assert manager.snapshot()["downloads"][0]["library_path"] == "C/A - B - C [720p].mp4"
        manager.shutdown()


# ── transcode() stopping ───────────────────────────────────────────────


@posix_only
def test_transcode_stops_when_cancelled(tmp_path, monkeypatch):
    fake = tmp_path / "HandBrakeCLI"
    fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr(handbrake, "HANDBRAKE", str(fake))
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    ok = handbrake.transcode(tmp_path / "in.mp4", tmp_path / "out.mp4", "p.json", "P",
                             cancel=cancel)
    assert ok is False and time.monotonic() - started < 10


# ── the loop ───────────────────────────────────────────────────────────


@needs("ffprobe")
def test_an_unplugged_library_marks_nothing_missing(tmp_path):
    root = tmp_path / "Volumes" / "Media"
    root.mkdir(parents=True)
    with LibraryDB.open(root) as db:
        db.upsert(Video(path="C/A - B - C.mp4"))
    with LibraryDB.open(root) as db:
        root.rename(tmp_path / "unplugged")                  # the disk goes away
        args = vidlib.build_parser().parse_args([str(root), "-O", "-n", "-S"])
        status = PipelineStatus()
        vidlib.run(root, db, args, status)
        assert status.snapshot()["state"] == "disk_missing"
        assert db.get("C/A - B - C.mp4").status == "sorted"


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_the_loop_reports_each_file_and_where_it_went(tmp_path, make_video, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))       # trash stays in the test
    root = tmp_path / "lib"
    make_video(root / "Alice - Day 1 - Lakes.mp4", "640x480")
    settled: list[tuple[str, str]] = []
    status = PipelineStatus()
    args = vidlib.build_parser().parse_args([str(root), "-O", "-S", "-g", "cpu"])
    with LibraryDB.open(root) as db:
        vidlib.run(root, db, args, status, lambda name, path: settled.append((name, path)))
    snap = status.snapshot()
    assert snap["batch_total"] == 1 and snap["batch_done"] == 1
    assert snap["recent"][0]["status"] == "sorted"
    assert settled == [("Alice - Day 1 - Lakes.mp4", "Lakes/Alice - Day 1 - Lakes [480p].mp4")]


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_stop_now_leaves_the_file_untouched_and_unfailed(tmp_path, make_video, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "lib"
    video = make_video(root / "Alice - Long - Lakes.mp4", "1280x720", seconds=30)
    before = video.stat().st_size
    status = PipelineStatus()
    real = handbrake.transcode

    def stop_midway(*a, **kw):
        threading.Timer(0.5, status.stop_now).start()
        return real(*a, **kw)
    monkeypatch.setattr(vidlib, "transcode", stop_midway)
    args = vidlib.build_parser().parse_args([str(root), "-O", "-S", "-g", "cpu"])
    with LibraryDB.open(root) as db:
        vidlib.run(root, db, args, status)
        assert video.exists() and video.stat().st_size == before
        assert not list(root.glob("*.tmp")) and not list(root.glob("*.processing"))
        row = db.get(video.name)
        assert row is None or row.failures == 0
    assert status.snapshot()["recent"][0]["status"] == "stopped"
    assert os.listdir(root)   # nothing else went missing
