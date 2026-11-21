"""The web page's Library: queries, thumbnails, rename and trash, over HTTP."""

from __future__ import annotations

import http.client
import json
from urllib.parse import quote

import pytest
from conftest import needs

from toolboxcli.vidlib.db import LibraryDB, Video
from toolboxcli.vidlib.downloads import DownloadManager
from toolboxcli.vidlib.server import VidlibServer
from toolboxcli.vidlib.status import PipelineStatus


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not p.name.startswith(".vidlib"))


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))      # the Trash stays in the test
    (tmp_path / "home").mkdir()
    root = tmp_path / "lib"
    rows = {
        "Lakes/Alice, Bob - Day 1 - Lakes [1080p].mp4": dict(height=1080, size=900),
        "Lakes/Bob and Alice - Day 1 - Lakes [720p].mp4": dict(height=720, size=500),
        "Peaks/Carol - Summit - Peaks [720p Restored].mp4": dict(height=720, size=300),
        "Odd name.mp4": dict(height=480, size=100),
        "_review/Alice - Parked - Lakes.mp4": dict(height=480, status="review",
                                                   reason="downgrade",
                                                   compared_with="Lakes/Alice, Bob - Day 1 - Lakes [1080p].mp4"),
    }
    root.mkdir()
    with LibraryDB.open(root) as db:
        for path, fields in rows.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(b"x" * fields.get("size", 10))
            db.upsert(Video(path=path, **fields))
        db.upsert(Video(path="Lakes/Gone - Away - Lakes.mp4", status="missing"))
    return root


@pytest.fixture
def client(library):
    db = LibraryDB.open(library, shared=True)
    manager = DownloadManager(library, db)
    status = PipelineStatus()
    server = VidlibServer(manager, port=0, pipeline=status)
    manager.start()
    server.start()
    port = server.server_address[1]

    def call(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        headers = {"Host": f"127.0.0.1:{port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        ctype = res.getheader("Content-Type", "")
        return res.status, (json.loads(raw) if "json" in ctype else raw)

    call.status = status
    yield call
    server.stop()
    manager.shutdown()
    db.close()


def _names(data):
    return [v["filename"] for v in data["videos"]]


def test_browsing_and_filtering(client):
    status, data = client("GET", "/api/library")
    assert status == 200
    assert _names(data) == [
        "Alice, Bob - Day 1 - Lakes [1080p].mp4", "Bob and Alice - Day 1 - Lakes [720p].mp4",
        "Carol - Summit - Peaks [720p Restored].mp4", "Odd name.mp4"]   # no review, no missing
    assert data["library"] == {"videos": 4, "size": 1800}
    assert {p["name"]: p["count"] for p in data["people"]} == {"Alice": 3, "Bob": 2, "Carol": 1}
    assert [c["name"] for c in data["collections"]] == ["Lakes", "Peaks"]

    assert _names(client("GET", "/api/library?person=carol")[1]) == [
        "Carol - Summit - Peaks [720p Restored].mp4"]
    assert len(client("GET", "/api/library?resolution=720p&collection=lakes")[1]["videos"]) == 1
    assert _names(client("GET", "/api/library?q=summit")[1]) == [
        "Carol - Summit - Peaks [720p Restored].mp4"]
    assert _names(client("GET", "/api/library?view=unnamed")[1]) == ["Odd name.mp4"]
    assert _names(client("GET", "/api/library?view=review")[1]) == ["Alice - Parked - Lakes.mp4"]
    dupes = client("GET", "/api/library?view=duplicates")[1]["videos"]
    assert len(dupes) == 2 and {v["group"] for v in dupes} == {0}
    assert client("GET", "/api/library?view=bogus")[0] == 409
    assert client("GET", "/api/library?offset=x")[0] == 400


def test_a_review_item_shows_its_library_copy(client):
    _, v = client("GET", "/api/library/video?path=" + quote("_review/Alice - Parked - Lakes.mp4"))
    assert v["library_copy"]["filename"] == "Alice, Bob - Day 1 - Lakes [1080p].mp4"


def test_only_files_the_library_knows_are_touched(client, library, tmp_path):
    (tmp_path / "secret.txt").write_text("no")
    for path in ("../secret.txt", "Lakes/Gone - Away - Lakes.mp4", "nope.mp4"):
        assert client("POST", "/api/library/trash", {"path": path})[0] == 409
        assert client("GET", f"/api/thumb?path={quote(path)}")[0] == 404
    assert (tmp_path / "secret.txt").exists()


def test_rename_moves_the_file_and_its_row_keeping_tags(client, library):
    status, moved = client("POST", "/api/library/rename", {
        "path": "Odd name.mp4", "people": "alice, Dee", "title": "Picnic",
        "collection": "lakes"})
    assert status == 200
    # The library's spellings, its existing Lakes folder, and the row followed the file.
    assert moved["path"] == "Lakes/Alice, Dee - Picnic - Lakes.mp4"
    assert (library / "Lakes" / "Alice, Dee - Picnic - Lakes.mp4").exists()
    assert not (library / "Odd name.mp4").exists()
    with LibraryDB.open(library) as db:
        row = db.get("Lakes/Alice, Dee - Picnic - Lakes.mp4")
        assert row is not None and row.height == 480
        assert db.get("Odd name.mp4") is None

    _, moved = client("POST", "/api/library/rename", {
        "path": "Peaks/Carol - Summit - Peaks [720p Restored].mp4", "people": "Carol",
        "title": "Summit at dawn", "collection": "New Trip"})
    assert moved["path"] == "New Trip/Carol - Summit at dawn - New Trip [720p Restored].mp4"

    status, err = client("POST", "/api/library/rename", {
        "path": "Lakes/Bob and Alice - Day 1 - Lakes [720p].mp4", "people": "Alice",
        "title": "Day 1", "collection": ""})
    assert status == 409 and "no collection" in err["error"]


def test_rename_refuses_to_overwrite(client, library):
    status, err = client("POST", "/api/library/rename", {
        "path": "Lakes/Bob and Alice - Day 1 - Lakes [720p].mp4", "people": "Alice, Bob",
        "title": "Day 1", "collection": "Lakes"})
    # Different tags, so a different name: allowed. Now make it collide outright.
    assert status == 200
    (library / "Peaks" / "Carol - Clash - Peaks.mp4").write_text("mine")
    with LibraryDB.open(library) as db:
        db.upsert(Video(path="Peaks/Carol - Other - Peaks.mp4"))
    (library / "Peaks" / "Carol - Other - Peaks.mp4").write_text("v")
    status, err = client("POST", "/api/library/rename", {
        "path": "Peaks/Carol - Other - Peaks.mp4", "people": "Carol", "title": "Clash",
        "collection": "Peaks"})
    assert status == 409 and "already exists" in err["error"]
    assert (library / "Peaks" / "Carol - Clash - Peaks.mp4").read_text() == "mine"


def test_a_file_the_pipeline_is_working_on_is_left_alone(client, library):
    client.status.begin_batch(["Odd name.mp4"])
    client.status.begin_file("Odd name.mp4")
    status, err = client("POST", "/api/library/trash", {"path": "Odd name.mp4"})
    assert status == 409 and "working on" in err["error"]
    assert (library / "Odd name.mp4").exists()


def test_trash_moves_to_the_trash_and_forgets_the_row(client, library):
    assert client("POST", "/api/library/trash", {"path": "Odd name.mp4"})[0] == 200
    assert not (library / "Odd name.mp4").exists()
    with LibraryDB.open(library) as db:
        assert db.get("Odd name.mp4") is None
        assert any(e["action"] == "trashed" for e in db.events())


@needs("ffmpeg", "ffprobe")
def test_thumbnails_are_made_once_and_cached(client, library, make_video):
    video = make_video(library / "Peaks" / "Dee - Real - Peaks.mp4", "640x360", seconds=2)
    with LibraryDB.open(library) as db:
        db.upsert(Video(path="Peaks/Dee - Real - Peaks.mp4", duration=2.0,
                        size=video.stat().st_size))
    status, image = client("GET", "/api/thumb?path=" + quote("Peaks/Dee - Real - Peaks.mp4"))
    assert status == 200 and image[:2] == b"\xff\xd8"          # a JPEG
    cached = list((library / ".vidlib-cache" / "thumbs").glob("*.jpg"))
    assert len(cached) == 1
    assert client("GET", "/api/thumb?path=" + quote("Peaks/Dee - Real - Peaks.mp4"))[1] == image
    # A file ffmpeg can't read gets a 404, not a crash.
    assert client("GET", "/api/thumb?path=" + quote("Odd name.mp4"))[0] == 404


# ── the review queue ───────────────────────────────────────────────────

PARKED = "_review/Alice - Parked - Lakes.mp4"
COPY = "Lakes/Alice, Bob - Day 1 - Lakes [1080p].mp4"


def _park(library, name="Alice, Bob - Day 1 - Lakes [720p].mp4", reason="ambiguous",
          backend="cpu"):
    """A file the pipeline parked against COPY: after its encode (backend set) by default."""
    path = f"_review/{name}"
    (library / path).write_bytes(b"new")
    with LibraryDB.open(library) as db:
        db.upsert(Video(path=path, status="review", reason=reason, compared_with=COPY,
                        height=720, backend=backend))
    return path


def test_the_review_list_pairs_each_file_with_its_library_copy(client, library):
    _park(library)
    _, data = client("GET", "/api/library/review")
    assert [i["filename"] for i in data["items"]] == [
        "Alice - Parked - Lakes.mp4", "Alice, Bob - Day 1 - Lakes [720p].mp4"]
    assert all(i["library_copy"]["path"] == COPY for i in data["items"])
    assert client("GET", "/api/state")[1]["review_count"] == 2


def test_keep_new_trashes_the_library_copy_and_releases_the_file(client, library):
    parked = _park(library)
    assert client("POST", "/api/library/decide", {"path": parked, "keep": "new"})[0] == 200
    assert not (library / COPY).exists()
    assert (library / "Alice, Bob - Day 1 - Lakes [720p].mp4").read_bytes() == b"new"
    with LibraryDB.open(library) as db:
        assert db.get(COPY) is None
        row = db.get("Alice, Bob - Day 1 - Lakes [720p].mp4")
        # Already encoded: back in the root as compressed, so the pipeline only sorts it.
        assert row is not None and row.status == "compressed" and row.compared_with == ""


def test_keep_new_on_a_never_encoded_downgrade_makes_it_new_input(client, library):
    assert client("POST", "/api/library/decide", {"path": PARKED, "keep": "new"})[0] == 200
    assert (library / "Alice - Parked - Lakes.mp4").exists()
    with LibraryDB.open(library) as db:
        assert db.get("Alice - Parked - Lakes.mp4") is None      # the pipeline will encode it


def test_keep_old_trashes_the_parked_file(client, library):
    assert client("POST", "/api/library/decide", {"path": PARKED, "keep": "old"})[0] == 200
    assert not (library / PARKED).exists() and (library / COPY).exists()
    with LibraryDB.open(library) as db:
        assert db.get(PARKED) is None


def test_keep_both_needs_a_different_video(client, library):
    parked = _park(library)
    status, err = client("POST", "/api/library/decide", {
        "path": parked, "keep": "both", "people": "Bob, Alice", "title": "day 1",
        "collection": "Lakes"})
    assert status == 409 and "same video" in err["error"]
    status, _ = client("POST", "/api/library/decide", {
        "path": parked, "keep": "both", "people": "Alice, Bob", "title": "Day 1 (Director's cut)",
        "collection": "Lakes"})
    assert status == 200
    assert (library / "Alice, Bob - Day 1 (Director's cut) - Lakes [720p].mp4").exists()
    assert (library / COPY).exists()


def test_deciding_needs_a_review_item(client):
    status, err = client("POST", "/api/library/decide", {"path": COPY, "keep": "new"})
    assert status == 409 and "isn't waiting in review" in err["error"]
    status, err = client("POST", "/api/library/decide", {"path": PARKED, "keep": "maybe"})
    assert status == 409


# ── naming help for the rename form ────────────────────────────────────


def test_the_fields_answer_offers_names_from_the_title_and_flags_a_taken_name(client):
    _, data = client("POST", "/api/name/fields", {
        "people": "", "title": "Carol and Bob at the Peaks", "collection": "",
        "path": "Odd name.mp4"})
    assert [(f["kind"], f["name"], f["title_without"]) for f in data["found"]] == [
        ("people", "Carol", "Bob at the Peaks"), ("people", "Bob", "Carol at the Peaks"),
        ("collection", "Peaks", "Carol and Bob at the")]
    assert data["same_as"] == []
    _, data = client("POST", "/api/name/fields", {
        "people": "bob, alice", "title": "day 1", "collection": "Lakes", "path": "Odd name.mp4"})
    assert sorted(data["same_as"]) == [
        "Lakes/Alice, Bob - Day 1 - Lakes [1080p].mp4", "Lakes/Bob and Alice - Day 1 - Lakes [720p].mp4"]
    # Renaming a video to its own name isn't a clash with itself.
    _, data = client("POST", "/api/name/fields", {
        "people": "Carol", "title": "Summit", "collection": "Peaks",
        "path": "Peaks/Carol - Summit - Peaks [720p Restored].mp4"})
    assert data["same_as"] == []


def test_the_title_answer_offers_names_too(client):
    _, data = client("POST", "/api/name", {"title": "Bob - Hiking with Carol - Mountains"})
    assert data["title"] == "Hiking with Carol"
    assert [(f["name"], f["title_without"]) for f in data["found"]] == [("Carol", "Hiking")]
    assert data["same_as"] == []


@needs("ffmpeg", "ffprobe")
def test_the_hover_filmstrip_is_made_once_and_cached(client, library, make_video):
    make_video(library / "Peaks" / "Dee - Real - Peaks.mp4", "640x360", seconds=3)
    with LibraryDB.open(library) as db:
        db.upsert(Video(path="Peaks/Dee - Real - Peaks.mp4", duration=3.0))
    path = quote("Peaks/Dee - Real - Peaks.mp4")
    status, image = client("GET", f"/api/strip?path={path}")
    assert status == 200 and image[:2] == b"\xff\xd8"
    import subprocess
    strip = next((library / ".vidlib-cache" / "thumbs").glob("*-strip12.jpg"))
    size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(strip)], capture_output=True, text=True).stdout
    assert size.strip() == "3840,180"                   # twelve 320-wide frames side by side
    assert client("GET", f"/api/strip?path={path}")[1] == image
    assert client("GET", "/api/strip?path=" + quote("Odd name.mp4"))[0] == 404   # no length
