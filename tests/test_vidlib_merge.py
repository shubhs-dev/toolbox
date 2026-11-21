"""Merging parts: the plan the page shows, and the merge job the pipeline runs."""

from __future__ import annotations

import pytest
from conftest import needs

from toolboxcli.vidlib import cli as vidlib
from toolboxcli.vidlib.db import LibraryDB, Video
from toolboxcli.vidlib.downloads import DownloadManager
from toolboxcli.vidlib.library import Library, LibraryError
from toolboxcli.vidlib.status import PipelineStatus


@pytest.fixture
def lib(tmp_path, monkeypatch, make_video):
    """A library holding two encoded parts and one raw one, and a Library on it."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))       # concatvid's Trash stays here
    (tmp_path / "home").mkdir()
    root = tmp_path / "lib"
    specs = [("Trips/Alice - Lake P1 - Trips.mp4", "640x360", "cpu"),
             ("Trips/Alice, Bob - Lake P2 - Crossovers.mp4", "640x360", "cpu"),
             ("lake part 3.mp4", "640x360", "")]
    root.mkdir()
    with LibraryDB.open(root) as db:
        for path, size, backend in specs:
            make_video(root / path, size, seconds=2)
            db.upsert(Video(path=path, height=360, duration=2.0, backend=backend))
    db = LibraryDB.open(root, shared=True)
    manager = DownloadManager(root, db)
    status = PipelineStatus()
    yield Library(root, db, manager.lock, status), root, status
    db.close()


def _ids(library, *paths):
    with library.lock:
        by_path = {v.path: i for i, v in library.db.videos_with_ids()}
    return [by_path[p] for p in paths]


@needs("ffmpeg", "ffprobe")
def test_the_plan_names_the_merge_and_says_nothing_needs_re_encoding(lib):
    library, _, _ = lib
    ids = _ids(library, "Trips/Alice - Lake P1 - Trips.mp4",
               "Trips/Alice, Bob - Lake P2 - Crossovers.mp4")
    plan = library.merge_plan(ids)
    assert plan["name"] == {"people": "Alice, Bob", "title": "Lake", "collection": "Trips"}
    assert plan["ext"] == ".mp4" and not plan["blocking"]
    assert [n["level"] for n in plan["notes"]] == ["ok"]
    with pytest.raises(LibraryError):
        library.merge_plan(ids[:1])


@needs("ffmpeg", "ffprobe")
def test_a_plan_says_which_part_is_re_encoded(lib, make_video):
    library, root, _ = lib
    make_video(root / "Trips" / "Alice - Lake P3 - Trips.mp4", "1280x720", seconds=2)
    with library.lock:
        library.db.upsert(Video(path="Trips/Alice - Lake P3 - Trips.mp4", height=720))
    plan = library.merge_plan(_ids(library, "Trips/Alice - Lake P1 - Trips.mp4",
                                   "Trips/Alice - Lake P3 - Trips.mp4"))
    assert any("Part 2" in n["text"] and "down from 720p" in n["text"] for n in plan["notes"])


@needs("ffmpeg", "ffprobe")
def test_encoded_parts_merge_into_the_root_for_sorting_only(lib):
    library, root, status = lib
    ids = _ids(library, "Trips/Alice - Lake P1 - Trips.mp4",
               "Trips/Alice, Bob - Lake P2 - Crossovers.mp4")
    job = library.queue_merge(ids, "Alice, Bob", "Lake", "Trips")
    with library.lock:
        vidlib.run_merge(root, library.db, status.merge(job["id"]), status)
    merged = status.merge(job["id"])
    assert merged.state == "done" and merged.output == "Alice, Bob - Lake - Trips.mp4"
    assert (root / merged.output).is_file()
    assert not (root / "Trips" / "Alice - Lake P1 - Trips.mp4").exists()     # trashed
    assert not list((root / "_incoming").iterdir())                          # staging gone
    with library.lock:
        row = library.db.get(merged.output)
        assert row is not None and row.status == "compressed" and row.backend == "cpu"
        assert library.db.get("Trips/Alice - Lake P1 - Trips.mp4") is None
    duration = vidlib.probe(root / merged.output)
    assert duration is not None and duration[1] == pytest.approx(4.0, abs=0.5)


@needs("ffmpeg", "ffprobe")
def test_parts_the_library_has_as_done_are_never_re_encoded(lib):
    # "lake part 3.mp4" has no encoder recorded — like a file vidlib found already sitting in
    # a folder, or one from the old optimiselib log. The pipeline counts it as compressed by
    # its status, so the merge must too: re-encoding it would only lose quality.
    library, root, status = lib
    ids = _ids(library, "Trips/Alice - Lake P1 - Trips.mp4", "lake part 3.mp4")
    job = library.queue_merge(ids, "Alice", "Lake", "")                 # blank → Unknown
    with library.lock:
        vidlib.run_merge(root, library.db, status.merge(job["id"]), status)
    merged = status.merge(job["id"])
    assert merged.state == "done" and merged.output == "Alice - Lake - Unknown.mp4"
    with library.lock:
        row = library.db.get(merged.output)
        assert row is not None and row.status == "compressed"       # sorted, not encoded
        assert vidlib.already_done(row, 8, "auto")


@needs("ffmpeg", "ffprobe")
def test_a_part_the_library_doesnt_have_as_done_makes_the_merge_new_input(lib):
    library, root, status = lib
    with library.lock:
        row = library.db.get("lake part 3.mp4")
        assert row is not None
        row.status = "failed"                     # e.g. its encode keeps failing
        library.db.upsert(row)
    job = status.queue_merge(["Trips/Alice - Lake P1 - Trips.mp4", "lake part 3.mp4"],
                             "Alice - Lake - Trips", ".mp4")
    with library.lock:
        vidlib.run_merge(root, library.db, job, status)
        assert job.state == "done" and library.db.get(job.output) is None   # gets encoded


@needs("ffmpeg", "ffprobe")
def test_a_failed_merge_leaves_the_parts_alone(lib):
    library, root, status = lib
    (root / "Trips" / "Alice - Lake P1 - Trips.mp4").write_bytes(b"not a video")
    job = status.queue_merge([p for p in ("Trips/Alice - Lake P1 - Trips.mp4",
                                          "Trips/Alice, Bob - Lake P2 - Crossovers.mp4")],
                             "Alice - Lake - Trips", ".mp4")
    with library.lock:
        vidlib.run_merge(root, library.db, job, status)
    assert job.state in ("failed", "unverified")
    assert (root / "Trips" / "Alice, Bob - Lake P2 - Crossovers.mp4").is_file()
    assert not (root / "Alice - Lake - Trips.mp4").exists()


def test_the_same_video_cant_be_in_two_queued_merges():
    status = PipelineStatus()
    status.queue_merge(["a.mp4", "b.mp4"], "X - Y - Z", ".mp4")
    with pytest.raises(ValueError, match="already being merged"):
        status.queue_merge(["b.mp4", "c.mp4"], "X - Y - Z", ".mp4")
