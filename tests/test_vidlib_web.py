"""vidlib's web server and download manager, driven over HTTP against a fake yt-dlp."""

from __future__ import annotations

import http.client
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

from toolboxcli.vidlib.db import Download, LibraryDB, Video
from toolboxcli.vidlib.downloads import LEGACY_QUEUE_FILE, DownloadManager, move_file
from toolboxcli.vidlib.server import VidlibServer

FAKE_YTDLP = """\
#!{python}
import pathlib, sys, time
args = sys.argv[1:]
url = args[-1]
if "--print" in args:
    if "notitle" in url:
        print("ERROR: no title here", file=sys.stderr)
        sys.exit(1)
    print("Fetched - Title - " + url.rsplit("/", 1)[-1])
    sys.exit(0)
if "fail" in url:
    print("ERROR: simulated failure", flush=True)
    sys.exit(1)
template = args[args.index("-o") + 1]
out = pathlib.Path(template.replace("%(title)s", "Video Title").replace("%(ext)s", "mp4")
                   .replace("%%", "%"))
partial = out.with_name(out.name + ".part")
for pct in (20, 60, 100):
    print(f"[download]  {{pct}}.0% of 1.00MiB at 3.00MiB/s ETA 00:01", flush=True)
    partial.write_text(url)
    time.sleep(2 if "slow" in url else 0.05)
partial.rename(out)
out.with_suffix(".en.vtt").write_text("WEBVTT")
"""

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake yt-dlp is a POSIX script")


@pytest.fixture
def fake_ytdlp(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "yt-dlp"
    script.write_text(FAKE_YTDLP.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


class Client:
    def __init__(self, server: VidlibServer) -> None:
        self.port = server.server_address[1]

    def request(self, method: str, path: str, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            return res.status, json.loads(raw)
        except ValueError:
            return res.status, raw

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def state(self):
        return self.get("/api/state")[1]

    def downloads(self):
        return self.state()["downloads"]


def until(condition, timeout: float = 10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    with LibraryDB.open(root) as db:
        db.upsert(Video(path="Lemonade Stand/DougDoug, Atrioc - Old - Lemonade Stand.mp4"))
    return root


@pytest.fixture
def serve(library):
    """Start a server on a free port: serve(dest=None) → (client, manager)."""
    started = []

    def start(dest: Path | None = None, title_fetcher=None):
        db = LibraryDB.open(library, shared=True)
        kwargs = {"title_fetcher": title_fetcher} if title_fetcher else {}
        manager = DownloadManager(library, db, dest=dest, workers=2, **kwargs)
        server = VidlibServer(manager, port=0)
        manager.start()
        server.start()
        started.append((server, manager, db))
        return Client(server), manager

    yield start
    for server, manager, db in started:
        server.stop()
        manager.shutdown()
        db.close()


# ── naming over the API ────────────────────────────────────────────────


def test_the_page_and_its_files_are_served(serve):
    client, _ = serve()
    for path, needle in (("/", b"vidlib"), ("/app.js", b"EventSource"), ("/app.css", b":root")):
        status, body = client.get(path)
        assert status == 200 and needle in body


def test_naming_guesses_roles_from_the_library(serve):
    client, _ = serve()
    status, data = client.post("/api/name", {
        "title": "dougdoug - Atri-oc - The Conspiracy Episode - Part 1 - Lemonade Stand | YouTube"})
    assert status == 200
    assert [s["role"] for s in data["segments"]] == [
        "people", "people", "title", "title", "collection", "skip"]
    assert [(p["text"], p["match"], p["suggestion"]) for p in data["people"]] == [
        ("DougDoug", "exact", ""), ("Atri-oc", "loose", "Atrioc")]
    assert data["stem"] == ("DougDoug, Atri-oc - The Conspiracy Episode - Part 1 - "
                            "Lemonade Stand")
    assert data["complete"]


def test_naming_with_chosen_roles_and_with_fields(serve):
    client, _ = serve()
    _, data = client.post("/api/name", {"title": "A - B - C", "roles": ["title", "collection", "people"]})
    assert data["stem"] == "C - A - B"
    _, data = client.post("/api/name", {"title": "A - B", "roles": ["nonsense", "x"]})
    assert [s["role"] for s in data["segments"]] == ["people", "title"]   # bad roles: guessed
    _, data = client.post("/api/name/fields", {"people": "Alice", "title": "T", "collection": ""})
    assert data["problems"] == ["no collection — vidlib won't be able to sort it"]


def test_suggestions(serve):
    client, _ = serve()
    assert client.get("/api/suggest?kind=people&q=doug")[1] == {"suggestions": ["DougDoug"]}
    assert client.get("/api/suggest?kind=collections&q=stand")[1] == {
        "suggestions": ["Lemonade Stand"]}
    assert client.get("/api/suggest?kind=bogus&q=x")[0] == 400


# ── what the server refuses ────────────────────────────────────────────


def test_foreign_hosts_origins_and_non_json_writes_are_refused(serve):
    client, _ = serve()
    assert client.get("/api/state", headers={"Host": "evil.example:80"})[0] == 403
    assert client.get("/api/state", headers={"Host": f"localhost:{client.port}"})[0] == 200
    status, _ = client.post("/api/downloads", {"url": "https://x/a", "name": "A - B - C"},
                            headers={"Origin": "https://evil.example"})
    assert status == 403
    status, _ = client.post("/api/downloads", {"url": "https://x/a"},
                            headers={"Content-Type": "text/plain"})
    assert status == 415
    assert client.state()["downloads"] == []
    assert client.get("/api/nothing")[0] == 404
    assert client.request("DELETE", "/api/downloads/99")[0] == 404


# ── downloading into the library ───────────────────────────────────────


@posix_only
def test_a_download_lands_in_the_library_root(serve, library, fake_ytdlp):
    client, _ = serve()
    status, _ = client.post("/api/downloads", {
        "url": "https://x/a", "name": "DougDoug - Day 1 - Lemonade Stand",
        "page_title": "DougDoug - Day 1 - Lemonade Stand | YouTube"})
    assert status == 201
    until(lambda: client.downloads()[0]["status"] == "imported")
    row = client.downloads()[0]
    assert row["library_path"] == "DougDoug - Day 1 - Lemonade Stand.mp4"
    assert (library / "DougDoug - Day 1 - Lemonade Stand.mp4").read_text() == "https://x/a"
    assert (library / "DougDoug - Day 1 - Lemonade Stand.en.vtt").exists()   # sidecar too
    assert list((library / "_incoming").iterdir()) == []                      # job folder gone
    assert client.state()["overall"]["percent"] == 100.0
    with LibraryDB.open(library) as db:
        assert db.downloads()[0].status == "imported"


@posix_only
def test_an_unnamed_download_uses_the_videos_own_title(serve, library, fake_ytdlp):
    client, _ = serve()
    client.post("/api/downloads", {"url": "https://x/b", "name": ""})
    until(lambda: client.downloads()[0]["status"] == "imported")
    assert (library / "Video Title.mp4").exists()


@posix_only
def test_a_name_clash_waits_instead_of_overwriting(serve, library, fake_ytdlp):
    (library / "A - B - C.mp4").write_text("mine")
    client, _ = serve()
    client.post("/api/downloads", {"url": "https://x/a", "name": "A - B - C"})
    until(lambda: client.downloads()[0]["status"] == "downloaded")
    row = client.downloads()[0]
    assert "already exists" in row["error"]
    assert (library / "A - B - C.mp4").read_text() == "mine"
    (library / "A - B - C.mp4").rename(library / "A - B - C (mine).mp4")
    assert client.post("/api/downloads/import")[1] == {"moved": 1, "problems": []}
    assert client.downloads()[0]["status"] == "imported"
    assert (library / "A - B - C.mp4").read_text() == "https://x/a"


@posix_only
def test_failures_can_be_retried_and_removed(serve, fake_ytdlp):
    client, _ = serve()
    client.post("/api/downloads", {"url": "https://x/fail", "name": "A - B - C"})
    until(lambda: client.downloads()[0]["status"] == "failed")
    row = client.downloads()[0]
    assert row["error"] == "ERROR: simulated failure"
    assert client.state()["overall"]["failed"] == 1
    assert client.post(f"/api/downloads/{row['id']}/retry")[0] == 200
    until(lambda: client.downloads()[0]["status"] == "failed")
    assert client.request("DELETE", f"/api/downloads/{row['id']}")[0] == 200
    assert client.downloads() == []


@posix_only
def test_the_same_url_cant_be_queued_twice_while_active(serve, fake_ytdlp):
    client, _ = serve()
    client.post("/api/downloads", {"url": "https://x/slow", "name": "A - B - C"})
    status, data = client.post("/api/downloads", {"url": "https://x/slow", "name": "A - B - C"})
    assert status == 409 and "already in the queue" in data["error"]


@posix_only
def test_cancelling_a_running_download(serve, library, fake_ytdlp):
    client, _ = serve()
    client.post("/api/downloads", {"url": "https://x/slow", "name": "A - B - C"})
    until(lambda: client.downloads()[0]["status"] == "downloading")
    until(lambda: client.downloads()[0]["percent"] > 0)
    row = client.downloads()[0]
    assert row["speed"] == "3.00MiB/s" and row["eta"] == "00:01"
    assert client.request("DELETE", f"/api/downloads/{row['id']}")[0] == 409   # cancel first
    client.post(f"/api/downloads/{row['id']}/cancel")
    until(lambda: client.downloads()[0]["status"] == "cancelled")
    assert not list(library.glob("*.mp4"))
    assert client.post("/api/downloads/clear")[1] == {"removed": 1}


@posix_only
def test_an_interrupted_queue_resumes_on_start(serve, library, fake_ytdlp):
    with LibraryDB.open(library) as db:
        db.save_download(Download("https://x/r", "A - Resumed - C", status="downloading"))
        db.save_download(Download("https://x/old", "Old", status="imported"))
    client, _ = serve()
    until(lambda: client.downloads()[0]["status"] == "imported")
    assert (library / "A - Resumed - C.mp4").exists()
    assert not (library / "Old.mp4").exists()                       # history isn't re-run


@posix_only
def test_shutting_down_leaves_running_downloads_to_resume(serve, library, fake_ytdlp):
    client, manager = serve()
    client.post("/api/downloads", {"url": "https://x/slow", "name": "A - B - C"})
    until(lambda: client.downloads()[0]["status"] == "downloading")
    manager.shutdown()
    time.sleep(0.3)
    with LibraryDB.open(library) as db:
        assert db.downloads()[0].status == "downloading"            # start() requeues it


# ── bulk paste ─────────────────────────────────────────────────────────


@posix_only
def test_bulk_urls_wait_for_a_name_with_their_titles_fetched(serve, library, fake_ytdlp):
    client, _ = serve()
    status, data = client.post("/api/downloads/bulk", {
        "urls": ["https://x/one", "https://x/notitle", "https://x/one", " "]})
    assert status == 201 and data == {"added": 2}
    until(lambda: not any(d["fetching"] for d in client.downloads()))
    one, bad = client.downloads()
    assert one["status"] == "naming" and one["page_title"] == "Fetched - Title - one"
    assert bad["status"] == "naming" and bad["error"] == "ERROR: no title here"
    assert client.state()["overall"]["naming"] == 2
    client.post(f"/api/downloads/{one['id']}/name", {"name": "A - One - C"})
    until(lambda: client.downloads()[0]["status"] == "imported")
    assert (library / "A - One - C.mp4").exists()
    status, _ = client.post(f"/api/downloads/{one['id']}/name", {"name": "again"})
    assert status == 409


# ── download-only mode ─────────────────────────────────────────────────


@posix_only
def test_download_only_waits_in_its_folder_until_moved(serve, library, tmp_path, fake_ytdlp):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    client, _ = serve(dest=elsewhere)
    assert client.state()["mode"] == "download-only"
    assert client.state()["library_name"] == library.name
    client.post("/api/downloads", {"url": "https://x/a", "name": "A - B - C"})
    until(lambda: client.downloads()[0]["status"] == "downloaded")
    row = client.downloads()[0]
    assert row["file"] == str(elsewhere / "A - B - C.mp4") and row["error"] == ""
    assert not (library / "A - B - C.mp4").exists()
    assert client.state()["overall"]["percent"] == 100.0
    assert client.post("/api/downloads/import")[1] == {"moved": 1, "problems": []}
    row = client.downloads()[0]
    assert row["status"] == "imported" and row["library_path"] == "A - B - C.mp4"
    assert (library / "A - B - C.mp4").read_text() == "https://x/a"
    assert not (elsewhere / "A - B - C.mp4").exists()


def test_an_old_video_download_queue_is_imported_once(serve, library, tmp_path):
    folder = tmp_path / "dl"
    folder.mkdir()
    (folder / "Alice - Day 1 - Lakes.mkv").write_text("v")
    (folder / LEGACY_QUEUE_FILE).write_text(json.dumps({"version": 1, "items": [
        {"url": "https://x/done", "name": "Alice - Day 1 - Lakes", "status": "done"},
        {"url": "https://x/bad", "name": "B", "status": "failed", "error": "HTTP 403"},
        {"name": "no url"},
    ]}))
    client, manager = serve(dest=folder)
    manager.shutdown()   # nothing queued here, but don't let anything start
    rows = client.downloads()
    assert [(r["url"], r["status"]) for r in rows] == [
        ("https://x/done", "downloaded"), ("https://x/bad", "failed")]
    assert rows[0]["file"] == str(folder / "Alice - Day 1 - Lakes.mkv")
    assert rows[1]["error"] == "HTTP 403"
    assert (folder / f"{LEGACY_QUEUE_FILE}.migrated").exists()
    assert not (folder / LEGACY_QUEUE_FILE).exists()


# ── moving files ───────────────────────────────────────────────────────


def test_move_file_never_overwrites_and_copies_across_disks(tmp_path, monkeypatch):
    src_dir, dest_dir = tmp_path / "a", tmp_path / "b"
    src_dir.mkdir()
    dest_dir.mkdir()
    (src_dir / "x.mp4").write_text("new")
    (dest_dir / "x.mp4").write_text("old")
    with pytest.raises(FileExistsError):
        move_file(src_dir / "x.mp4", dest_dir)
    assert (dest_dir / "x.mp4").read_text() == "old"

    (dest_dir / "x.mp4").unlink()
    import toolboxcli.vidlib.downloads as downloads

    def no_rename(*_args):
        raise OSError("cross-device link")   # what a rename between disks does
    monkeypatch.setattr(downloads.os, "rename", no_rename)
    moved = move_file(src_dir / "x.mp4", dest_dir)
    assert moved.read_text() == "new" and not (src_dir / "x.mp4").exists()
    assert [p.name for p in dest_dir.iterdir()] == ["x.mp4"]   # no temp file left


def test_a_garbled_request_or_hang_up_prints_no_traceback(serve, capsys):
    import socket
    client, _ = serve()
    with socket.create_connection(("127.0.0.1", client.port)) as sock:
        sock.sendall(b"\x16\x03\x01 not http at all\r\n\r\n")
        sock.close()
    time.sleep(0.3)
    assert client.get("/api/state")[0] == 200            # still serving
    assert "Traceback" not in capsys.readouterr().err


def test_an_unplugged_target_is_never_recreated(tmp_path):
    from toolboxcli.vidlib import fetcher
    gone = tmp_path / "Volumes" / "Media"                  # the disk's mount point, missing
    with fetcher.Engine(1) as eng:
        job = eng.submit(fetcher.Job(1, "https://x/a", gone / "_incoming" / "1")).result()
    assert job.returncode == -1 and not (tmp_path / "Volumes").exists()
    src = tmp_path / "a.mp4"
    src.write_text("v")
    with pytest.raises(FileNotFoundError, match="disk connected"):
        move_file(src, gone)
    assert src.exists() and not (tmp_path / "Volumes").exists()


# ── "you downloaded this before" ───────────────────────────────────────


@pytest.mark.parametrize("a, b", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?t=42"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1", "https://youtube.com/shorts/dQw4w9WgXcQ"),
    ("https://Example.com/videos/123/", "https://example.com/videos/123#comments"),
])
def test_links_to_the_same_video_share_a_key(a, b):
    from toolboxcli.vidlib.downloads import url_key
    assert url_key(a) == url_key(b)


def test_different_videos_dont():
    from toolboxcli.vidlib.downloads import url_key
    assert url_key("https://youtu.be/dQw4w9WgXcQ") != url_key("https://youtu.be/aaaaaaaaaaa")
    assert url_key("https://example.com/v?id=1") != url_key("https://example.com/v?id=2")


def test_an_earlier_download_is_reported_even_after_clearing(serve, library):
    with LibraryDB.open(library) as db:
        db.save_download(Download("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                  "A - Song - Clips", status="imported",
                                  library_path="Clips/A - Song - Clips [1080p].mp4",
                                  added_at="2026-11-14T10:00:00"))
        db.save_download(Download("https://x/failed", "B", status="failed"))
        (library / "Clips").mkdir()
        (library / "Clips" / "A - Song - Clips [1080p].mp4").write_bytes(b"v")
        db.upsert(Video(path="Clips/A - Song - Clips [1080p].mp4"))
    client, _ = serve()
    assert any(d["status"] == "imported" for d in client.downloads())
    assert client.post("/api/downloads/clear")[1] == {"removed": 1}
    assert all(d["status"] != "imported" for d in client.downloads())        # off the list
    _, data = client.get("/api/downloads/seen?url=" + quote("https://youtu.be/dQw4w9WgXcQ?t=5"))
    [seen] = data["seen"]                                                   # …but remembered
    assert seen["name"] == "A - Song - Clips" and seen["present"]
    assert seen["where"] == "Clips/A - Song - Clips [1080p].mp4"
    assert client.get("/api/downloads/seen?url=" + quote("https://x/failed"))[1]["seen"] == []
    assert client.get("/api/downloads/seen?url=")[1]["seen"] == []
    (library / "Clips" / "A - Song - Clips [1080p].mp4").unlink()
    [seen] = client.get("/api/downloads/seen?url=" + quote("https://youtu.be/dQw4w9WgXcQ"))[1]["seen"]
    assert not seen["present"]
