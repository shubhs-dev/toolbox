"""vidlib -w: renaming a Title - People - Collection library to People - Title - Collection."""

from __future__ import annotations

import pytest

from toolboxcli.vidlib import cli as vidlib, core
from toolboxcli.vidlib.db import LibraryDB, Video


@pytest.mark.parametrize("stem, swapped", [
    ("Day 1 - Alice - Lakes", "Alice - Day 1 - Lakes"),
    ("Day 1 - Alice, Bob - Lakes [Restored 1080p 10bit]",
     "Alice, Bob - Day 1 - Lakes [Restored 1080p 10bit]"),
    ("Day 1 - Part 2 - Alice - Lakes", "Alice - Day 1 - Part 2 - Lakes"),   # title moves whole
    ("Day 1 - Alice - Lakes - Sub", "Alice - Day 1 - Lakes - Sub"),        # addsub's marker stays
    ("Day 1 - Alice - Lakes.en", "Alice - Day 1 - Lakes.en"),              # a subtitle's stem
    ("Movie - Director's Cut", None),
    ("IMG_2041", None),
])
def test_swap_title_people(stem, swapped):
    assert core.swap_title_people(stem) == swapped
    if swapped is not None and "Part" not in stem:
        assert core.swap_title_people(swapped) == stem   # undoes itself, for a one-field title


def _library(tmp_path):
    root = tmp_path / "lib"
    (root / "Lakes").mkdir(parents=True)
    (root / "_review").mkdir()
    files = {
        "Lakes/Day 1 - Alice - Lakes [1080p].mp4": "compressed",
        "Lakes/Day 2 - Alice, Bob - Lakes [720p].mkv": "compressed",
        "_review/Day 1 - Alice - Lakes [720p].mp4": "review",
        "Night Swim - Carol - Lakes.mp4": None,            # waiting in the root, no row
        "Night Swim - Carol - Lakes.en.srt": None,         # its subtitle
        "IMG_2041.mp4": None,                              # not three fields
    }
    for rel in files:
        (root / rel).write_bytes(b"x")
    db = LibraryDB.open(root)
    for rel, status in files.items():
        if status:
            st = (root / rel).stat()
            db.upsert(Video(path=rel, status=status, size=st.st_size, mtime=st.st_mtime,
                            compared_with="Lakes/Day 1 - Alice - Lakes [1080p].mp4"
                            if status == "review" else ""))
    return root, db


def test_swap_order_renames_files_and_moves_their_rows(tmp_path, monkeypatch):
    root, db = _library(tmp_path)
    monkeypatch.setattr(vidlib, "confirm", lambda *a, **k: "y")
    with db:
        vidlib.swap_order(root, db)
        names = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                       if p.is_file() and not p.name.startswith("."))
        assert names == [
            "Carol - Night Swim - Lakes.en.srt",
            "Carol - Night Swim - Lakes.mp4",
            "IMG_2041.mp4",
            "Lakes/Alice - Day 1 - Lakes [1080p].mp4",
            "Lakes/Alice, Bob - Day 2 - Lakes [720p].mkv",
            "_review/Alice - Day 1 - Lakes [720p].mp4",
        ]
        rows = {v.path: v for v in db.all()}
        assert set(rows) == {"Lakes/Alice - Day 1 - Lakes [1080p].mp4",
                             "Lakes/Alice, Bob - Day 2 - Lakes [720p].mkv",
                             "_review/Alice - Day 1 - Lakes [720p].mp4"}
        moved = rows["Lakes/Alice, Bob - Day 2 - Lakes [720p].mkv"]
        assert moved.status == "compressed" and moved.name.people == ("Alice", "Bob")
        assert moved.name.title == "Day 2"
        # The parked copy still points at the library copy it was compared with.
        assert rows["_review/Alice - Day 1 - Lakes [720p].mp4"].compared_with == \
            "Lakes/Alice - Day 1 - Lakes [1080p].mp4"
        people, _ = db.spellings()
        assert "Day 1" not in people and "Alice" in people
        assert db.last_event(vidlib.SWAP_EVENT) is not None


def test_swap_order_dry_run_and_cancel_change_nothing(tmp_path, monkeypatch):
    root, db = _library(tmp_path)
    before = sorted(p.name for p in root.rglob("*"))
    with db:
        vidlib.swap_order(root, db, dry_run=True)
        monkeypatch.setattr(vidlib, "confirm", lambda *a, **k: "n")
        vidlib.swap_order(root, db)
        assert sorted(p.name for p in root.rglob("*")) == before
        assert db.last_event(vidlib.SWAP_EVENT) is None


def test_swap_order_never_overwrites(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    (root / "Day 1 - Alice - Lakes.mp4").write_bytes(b"a")
    (root / "Alice - Day 1 - Lakes.mp4").write_bytes(b"b")      # already the right way round
    renames, skipped = vidlib.plan_swap(root)
    # They'd trade places — fine, both move through a temporary name.
    assert {(s.name, d.name) for s, d in renames} == {
        ("Day 1 - Alice - Lakes.mp4", "Alice - Day 1 - Lakes.mp4"),
        ("Alice - Day 1 - Lakes.mp4", "Day 1 - Alice - Lakes.mp4"),
    }
    (root / "Lakes - Bob - X.mp4").write_bytes(b"c")
    (root / "Bob - Lakes - X.mp4").mkdir()                      # in the way, not moving
    monkeypatch.setattr(vidlib, "confirm", lambda *a, **k: "y")
    renames, skipped = vidlib.plan_swap(root)
    assert ("Lakes - Bob - X.mp4", "Bob - Lakes - X.mp4 already exists") in {
        (f.name, why) for f, why in skipped}
    db = LibraryDB.open(root)
    with db:
        vidlib.swap_order(root, db)
    assert (root / "Alice - Day 1 - Lakes.mp4").read_bytes() == b"a"
    assert (root / "Day 1 - Alice - Lakes.mp4").read_bytes() == b"b"
    assert (root / "Lakes - Bob - X.mp4").read_bytes() == b"c"
