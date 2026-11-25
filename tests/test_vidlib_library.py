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
    # A picked person or collection is that one only; partial matching is the search box's.
    assert client("GET", "/api/library?person=car")[1]["videos"] == []
    assert client("GET", "/api/library?collection=lake")[1]["videos"] == []
    assert _names(client("GET", "/api/library?q=summit")[1]) == [
        "Carol - Summit - Peaks [720p Restored].mp4"]
    assert _names(client("GET", "/api/library?view=unnamed")[1]) == ["Odd name.mp4"]
    assert _names(client("GET", "/api/library?view=review")[1]) == ["Alice - Parked - Lakes.mp4"]
    # Sorting applies to the filtered result; each order has its own starting direction.
    data = client("GET", "/api/library?sort=size")[1]
    assert data["sort"] == "size" and data["desc"] is True
    assert [v["size"] for v in data["videos"]] == [900, 500, 300, 100]
    assert [v["size"] for v in client("GET", "/api/library?sort=size&order=asc")[1]["videos"]] == [
        100, 300, 500, 900]
    assert _names(client("GET", "/api/library?person=Alice&sort=size&order=asc")[1]) == [
        "Bob and Alice - Day 1 - Lakes [720p].mp4", "Alice, Bob - Day 1 - Lakes [1080p].mp4"]
    assert client("GET", "/api/library?sort=colour")[0] == 409   # as an unknown view is
    assert client("GET", "/api/library?order=sideways")[0] == 400
    dupes = client("GET", "/api/library?view=duplicates")[1]["videos"]
    assert len(dupes) == 2 and {v["group"] for v in dupes} == {0}
    assert client("GET", "/api/library?view=bogus")[0] == 409
    assert client("GET", "/api/library?offset=x")[0] == 400


def test_duplicates_say_why_and_a_picture_match_can_be_dismissed(client, library):
    fp = ",".join(f"{0x0F0F0F0F0F0F0F0F + i:016x}" for i in range(12))
    with LibraryDB.open(library) as db:
        for path in ("Peaks/Carol - Summit - Peaks [720p Restored].mp4", "Odd name.mp4"):
            row = db.get(path)
            assert row is not None
            row.duration, row.fingerprint = 300.0, fp
            db.upsert(row)
    dupes = client("GET", "/api/library?view=duplicates")[1]["videos"]
    why = {v["filename"]: (v["why"], v["why_text"], len(v["group_ids"])) for v in dupes}
    assert why["Odd name.mp4"] == ("picture", "same picture, different names", 2)
    assert why["Bob and Alice - Day 1 - Lakes [720p].mp4"][0] == "name"
    ids = next(v["group_ids"] for v in dupes if v["why"] == "picture")
    assert client("POST", "/api/library/duplicates/dismiss", {"ids": ids})[0] == 200
    left = client("GET", "/api/library?view=duplicates")[1]["videos"]
    assert {v["why"] for v in left} == {"name"}
    assert client("POST", "/api/library/duplicates/dismiss", {"ids": ["x"]})[0] == 400
    assert client("POST", "/api/library/duplicates/dismiss", {"ids": [ids[0]]})[0] >= 400


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

    # A blank field is "Unknown" when renaming.
    status, moved = client("POST", "/api/library/rename", {
        "path": "Lakes/Bob and Alice - Day 1 - Lakes [720p].mp4", "people": "Alice",
        "title": "Day 1", "collection": ""})
    assert status == 200 and moved["path"] == "Unknown/Alice - Day 1 - Unknown [720p].mp4"


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
    # Just the one frame (the indexer makes the filmstrip and fingerprint), named after the
    # file's size and mtime rather than its path, so renaming or filing it keeps it.
    cached = [p.name for p in (library / ".vidlib-cache" / "thumbs").glob("*.jpg")]
    assert len(cached) == 1 and "strip" not in cached[0]
    status, strip = client("GET", "/api/strip?path=" + quote("Peaks/Dee - Real - Peaks.mp4"))
    assert status == 200 and strip[:2] == b"\xff\xd8"
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


def test_renaming_fills_blank_fields_with_unknown(client, library):
    _, data = client("POST", "/api/name/fields", {
        "people": "", "title": "Picnic", "collection": " ", "fill_unknown": True,
        "path": "Odd name.mp4"})
    assert data["stem"] == "Unknown - Picnic - Unknown" and data["complete"]
    _, data = client("POST", "/api/name/fields", {"people": "", "title": "Picnic",
                                                  "collection": ""})
    assert not data["complete"]                         # a download doesn't fill them in
    status, moved = client("POST", "/api/library/rename", {
        "path": "Odd name.mp4", "people": "Carol", "title": "", "collection": "Clips"})
    assert status == 200 and moved["path"] == "Clips/Carol - Unknown - Clips.mp4"


# ── parts of one video ─────────────────────────────────────────────────


def test_parts_are_offered_and_a_dismissal_sticks(client, library):
    with LibraryDB.open(library) as db:
        for path in ("Lakes/Alice - Trip P1 - Lakes.mp4", "Peaks/Alice, Bob - Trip Part 2 - Peaks.mp4",
                     "trip part 3.mp4"):
            (library / path).parent.mkdir(exist_ok=True)
            (library / path).write_bytes(b"x")
            db.upsert(Video(path=path, duration=60.0))
    _, data = client("GET", "/api/library/parts")
    [group] = data["groups"]
    assert group["sure"] and group["gaps"] == [] and group["doubled"] == []
    assert [(m["filename"], m["first"]) for m in group["members"]] == [
        ("Alice - Trip P1 - Lakes.mp4", 1), ("Alice, Bob - Trip Part 2 - Peaks.mp4", 2),
        ("trip part 3.mp4", 3)]
    assert group["name"] == {"people": "Alice, Bob", "title": "Trip", "collection": "Lakes"}

    assert client("POST", "/api/library/parts/dismiss", {"ids": group["ids"]})[0] == 200
    assert client("GET", "/api/library/parts")[1]["groups"] == []
    assert client("POST", "/api/library/parts/dismiss", {"ids": [1]})[0] == 409
    assert client("POST", "/api/library/parts/dismiss", {"ids": "x"})[0] == 400



def test_rename_wont_file_into_an_ignored_folder(client, library):
    (library / "Peaks" / ".vidlib-ignore").write_text("")
    status, err = client("POST", "/api/library/rename", {
        "path": "Odd name.mp4", "people": "Dee", "title": "Hike", "collection": "Peaks"})
    assert status == 409 and ".vidlib-ignore" in err["error"]
    assert (library / "Odd name.mp4").exists()


def test_sort_orders_put_videos_with_nothing_to_sort_by_last():
    from toolboxcli.vidlib.library import sort_videos
    videos = [
        Video(path="b.mp4", size=0, duration=30, mtime=200),
        Video(path="Peaks/Carol - Hike - Peaks.mp4", size=50, duration=0, mtime=100),
        Video(path="Lakes/Alice - Swim - Lakes.mp4", size=10, duration=90, mtime=300),
        Video(path="a.mp4", size=10, duration=10, mtime=0),
    ]
    order = lambda *a: [v.filename for v in sort_videos(videos, *a)]   # noqa: E731
    assert order("name") == ["a.mp4", "Alice - Swim - Lakes.mp4", "b.mp4", "Carol - Hike - Peaks.mp4"]
    assert order("size") == ["Carol - Hike - Peaks.mp4", "a.mp4", "Alice - Swim - Lakes.mp4", "b.mp4"]
    assert order("size", False) == ["a.mp4", "Alice - Swim - Lakes.mp4", "Carol - Hike - Peaks.mp4", "b.mp4"]
    assert order("duration") == ["Alice - Swim - Lakes.mp4", "b.mp4", "a.mp4", "Carol - Hike - Peaks.mp4"]
    assert order("added") == ["Alice - Swim - Lakes.mp4", "b.mp4", "Carol - Hike - Peaks.mp4", "a.mp4"]
    # No collection (a badly named file) sorts after the named ones, either way round.
    assert order("collection") == ["Alice - Swim - Lakes.mp4", "Carol - Hike - Peaks.mp4", "a.mp4", "b.mp4"]
    assert order("collection", True) == ["Carol - Hike - Peaks.mp4", "Alice - Swim - Lakes.mp4", "a.mp4", "b.mp4"]
    assert order("people") == ["Alice - Swim - Lakes.mp4", "Carol - Hike - Peaks.mp4", "a.mp4", "b.mp4"]
