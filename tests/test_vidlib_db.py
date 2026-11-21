"""vidlib's library identity rules (core) and its SQLite database (db)."""

from __future__ import annotations

import json

import pytest

from toolboxcli.vidlib import core
from toolboxcli.vidlib.db import (
    DB_NAME,
    JSON_LOG_NAME,
    LEGACY_DB_NAME,
    Download,
    LibraryDB,
    Video,
    import_json_log,
    legacy_db_conflict,
)

# ── Identity rules ─────────────────────────────────────────────────────


@pytest.mark.parametrize("field, people", [
    ("Alice", ["Alice"]),
    ("Alice, Bob", ["Alice", "Bob"]),
    ("Alice,Bob ,  Carol", ["Alice", "Bob", "Carol"]),
    ("Alice and Bob", ["Alice", "Bob"]),
    ("Alice, Bob And Carol", ["Alice", "Bob", "Carol"]),
    ("Alice & Bob", ["Alice", "Bob"]),
    ("Andrew and Sandy", ["Andrew", "Sandy"]),  # "and" only as a whole word
    ("Brandon", ["Brandon"]),
    ("", []),
])
def test_split_people(field, people):
    assert core.split_people(field) == people


@pytest.mark.parametrize("a, b", [
    ("Alice, Bob - Day 1 - Lakes", "Bob, Alice - Day 1 - Lakes"),
    ("Alice, Bob - Day 1 - Lakes", "bob and ALICE - day 1 - lakes"),
    ("Alice, Bob and Carol - Day 1 - Lakes", "Carol & Alice, Bob - Day 1 - Lakes"),
    ("Alice - Day 1 - Lakes [720p]", "Alice - Day 1 - Lakes [1080p 10bit Sub]"),  # tags ignored
    ("Alice -  Day  1 - Lakes", "Alice - Day 1 - Lakes"),
    ("Alice - Day 1 - Lakes - Sub", "Alice - Day 1 - Lakes"),  # addsub -u's marker
    ("Alice, Alice - Day 1 - Lakes", "Alice - Day 1 - Lakes"),
])
def test_same_video(a, b):
    assert core.parse_name(a).key == core.parse_name(b).key


@pytest.mark.parametrize("a, b", [
    ("Alice - Day 1 - Lakes", "Alice - Day 2 - Lakes"),          # different title
    ("Alice - Day 1 - Lakes", "Alice - Day 1 - Peaks"),          # different collection
    ("Alice - Day 1 - Lakes", "Alice, Bob - Day 1 - Lakes"),     # different people
    ("Alice - Day 1 - Lakes", "Alice - Day 1"),                  # no collection
])
def test_different_video(a, b):
    assert core.parse_name(a).key != core.parse_name(b).key


@pytest.mark.parametrize("stem, title", [
    ("Movie - Director's Cut", "Movie - Director's Cut"),   # no person called "Movie"
    ("Alice - Swim [720p]", "Alice - Swim"),
    ("Just a clip [1080p]", "Just a clip"),
    ("IMG_1234", "IMG_1234"),
    (" - Title - Lakes", "- Title - Lakes"),                # empty People field
    ("Alice -  - Lakes", "Alice -  - Lakes"),               # empty Title field
])
def test_names_not_following_the_convention_are_not_split(stem, title):
    name = core.parse_name(stem)
    assert name.people == () and name.collection == "" and not name.complete
    assert core.normalize(name.title) == core.normalize(title)
    assert name.key is None


def test_parse_name_fields():
    name = core.parse_name("Alice and Bob - Day 1 - Part 2 - Lakes [Restored 1080p]")
    assert name.people == ("Alice", "Bob")
    assert name.title == "Day 1 - Part 2"
    assert name.collection == "Lakes"
    assert name.tags == ("Restored", "1080p")
    assert core.parse_name("Just a title") == core.VideoName((), "Just a title", "", ())


# ── Database ───────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    with LibraryDB.open(tmp_path) as database:
        yield database


def _library(db: LibraryDB) -> None:
    for path, height, status in [
        ("Lakes/Alice, Bob - Day 1 - Lakes [1080p].mp4", 1080, "sorted"),
        ("Lakes/Bob and Alice - Day 1 - Lakes [720p].mp4", 720, "sorted"),   # duplicate
        ("Lakes/Carol - Swim - Lakes [720p Restored].mp4", 720, "sorted"),
        ("Peaks/alice - Summit - Peaks [2160p 10bit].mkv", 2160, "sorted"),
        ("Gone - Old - Lakes [480p].mp4", 480, "missing"),
    ]:
        db.upsert(Video(path=path, height=height, status=status))


def test_upsert_and_get_round_trip(db):
    db.upsert(Video(path="Lakes/Alice - Day 1 - Lakes [1080p].mp4", height=1080,
                    has_sub=True, duration=12.5, size=100, mtime=5.0, backend="amd"))
    video = db.get("Lakes/Alice - Day 1 - Lakes [1080p].mp4")
    assert video is not None
    assert (video.height, video.has_sub, video.duration, video.backend) == (1080, True, 12.5, "amd")
    assert video.resolution == "1080p" and video.name.collection == "Lakes"
    assert video.updated_at  # stamped on write

    video.status = "review"
    db.upsert(video)
    again = db.get(video.path)
    assert again is not None and again.status == "review"
    assert len(db.all()) == 1


def test_search_filters(db):
    _library(db)
    paths = lambda videos: sorted(v.filename for v in videos)  # noqa: E731
    assert paths(db.search(person="ALICE")) == [
        "Alice, Bob - Day 1 - Lakes [1080p].mp4",
        "Bob and Alice - Day 1 - Lakes [720p].mp4",
        "alice - Summit - Peaks [2160p 10bit].mkv",
    ]
    assert paths(db.search(person="car")) == ["Carol - Swim - Lakes [720p Restored].mp4"]
    assert len(db.search(resolution="720")) == 2
    assert len(db.search(resolution="720P")) == 2
    assert len(db.search(collection="lakes")) == 3          # the missing one is excluded
    assert paths(db.search(tag="restored")) == ["Carol - Swim - Lakes [720p Restored].mp4"]
    assert paths(db.search(person="alice", resolution="720p")) == [
        "Bob and Alice - Day 1 - Lakes [720p].mp4"]
    assert db.search(person="nobody") == []
    assert db.search(person="%") == []                      # LIKE wildcards are literal


def test_duplicates_ignore_people_order_case_and_tags(db):
    _library(db)
    groups = db.duplicate_groups()
    assert [sorted(v.filename for v in g) for g in groups] == [[
        "Alice, Bob - Day 1 - Lakes [1080p].mp4",
        "Bob and Alice - Day 1 - Lakes [720p].mp4",
    ]]
    copies = db.same_video("Alice and Bob - day 1 - LAKES [Sub]")
    assert len(copies) == 2
    one = db.same_video("Alice, Bob - Day 1 - Lakes", exclude_path=copies[0].path)
    assert [v.path for v in one] == [copies[1].path]


def test_same_video_finds_copies_in_any_folder(db):
    db.upsert(Video(path="Lakes/Alice - Day 1 - Lakes [720p].mp4", height=720))
    db.upsert(Video(path="Staging/Alice - Day 1 - Lakes.mp4", height=1080))
    assert len(db.same_video("Alice - Day 1 - Lakes")) == 2


def test_move_keeps_the_row_and_reindexes_the_name(db):
    db.upsert(Video(path="Alice - Day 1.mp4", height=720, size=5, mtime=1.0))
    db.move("Alice - Day 1.mp4", "Lakes/Alice - Day 1 - Lakes [720p].mp4")
    assert db.get("Alice - Day 1.mp4") is None
    moved = db.get("Lakes/Alice - Day 1 - Lakes [720p].mp4")
    assert moved is not None and moved.size == 5 and moved.name.collection == "Lakes"
    assert len(db.search(collection="lakes")) == 1


def test_same_file_matches_size_and_mtime(db):
    db.upsert(Video(path="A - B - C.mp4", size=1234, mtime=1700000000.123456))
    assert len(db.same_file(1234, 1700000000.123456)) == 1
    assert db.same_file(1234, 1700000001.0) == []
    assert db.same_file(1235, 1700000000.123456) == []


def test_people_are_stored_once_and_unlinked_on_remove(db):
    db.upsert(Video(path="Alice - X - C.mp4"))
    db.upsert(Video(path="alice, Bob - Y - C.mp4"))
    people = {r["key"] for r in db.conn.execute("SELECT key FROM people")}
    assert people == {"alice", "bob"}
    db.remove("Alice - X - C.mp4")
    links = db.conn.execute("SELECT count(*) FROM video_people").fetchone()[0]
    assert links == 2


def test_unnamed_files_are_listable_but_never_duplicates(db):
    db.upsert(Video(path="Holiday [720p].mp4", height=720))
    db.upsert(Video(path="Old/Holiday [1080p].mp4", height=1080))
    db.upsert(Video(path="Movie - Director's Cut.mp4"))
    db.upsert(Video(path="Lakes/Alice - Day 1 - Lakes.mp4"))
    assert db.duplicate_groups() == []
    assert db.same_video("Holiday") == []
    assert sorted(v.filename for v in db.search(unnamed=True)) == [
        "Holiday [1080p].mp4", "Holiday [720p].mp4", "Movie - Director's Cut.mp4"]
    people = {r["key"] for r in db.conn.execute("SELECT key FROM people")}
    assert people == {"alice"}                                  # no "movie"


def test_upgrading_a_v1_database_re_derives_names_and_drops_junk_people(tmp_path):
    # A database written by the first version, which split every " - " name into fields.
    with LibraryDB.open(tmp_path) as db:
        db.upsert(Video(path="Lakes/Alice - Day 1 - Lakes.mp4"))
        db.upsert(Video(path="Movie - Director's Cut.mp4"))
        cut = db.conn.execute("SELECT id FROM videos WHERE path LIKE 'Movie%'").fetchone()[0]
        db.conn.execute("UPDATE videos SET people_key='movie', title_key=\"director's cut\" "
                        "WHERE id = ?", (cut,))
        db.conn.execute("INSERT INTO people (name, key) VALUES ('Movie', 'movie')")
        db.conn.execute("INSERT INTO video_people SELECT ?, id FROM people WHERE key='movie'",
                        (cut,))
        db.conn.execute("DROP TABLE downloads")              # added in schema 3
        db.conn.execute("DROP TABLE part_dismissals")        # added in schema 4
        db.conn.execute("PRAGMA user_version = 1")
        db.conn.commit()

    with LibraryDB.open(tmp_path) as db:
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 5
        row = db.conn.execute("SELECT people_key, title, title_key FROM videos "
                              "WHERE path LIKE 'Movie%'").fetchone()
        assert tuple(row) == ("", "Movie - Director's Cut", "")
        people = {r["key"] for r in db.conn.execute("SELECT key FROM people")}
        assert people == {"alice"}
        assert len(db.search(person="alice")) == 1



def test_upgrading_adds_the_downloads_table(tmp_path):
    with LibraryDB.open(tmp_path) as db:
        db.conn.execute("DROP TABLE downloads")
        db.conn.execute("DROP TABLE part_dismissals")
        db.conn.execute("PRAGMA user_version = 2")
        db.conn.commit()
    with LibraryDB.open(tmp_path) as db:
        saved = db.save_download(Download("https://x/a", "Alice - Day 1 - Lakes"))
        assert saved.id is not None and saved.added_at
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_downloads_round_trip_and_feed_the_spellings(tmp_path):
    with LibraryDB.open(tmp_path) as db:
        db.upsert(Video(path="Lakes/Alice, Bob - Day 1 - Lakes.mp4"))
        dl = db.save_download(Download("https://x/a", "Carol - Hike - Peaks"))
        db.save_download(Download("https://x/b", "Untitled thing"))
        dl.status, dl.error = "failed", "HTTP 403"
        db.save_download(dl)
        assert [(d.url, d.status, d.error) for d in db.downloads()] == [
            ("https://x/a", "failed", "HTTP 403"), ("https://x/b", "queued", "")]
        people, collections = db.spellings()
        assert people == ["Alice", "Bob", "Carol"] and collections == ["Lakes", "Peaks"]
        db.delete_download(dl.id)
        assert [d.url for d in db.downloads()] == ["https://x/b"]

def test_reopening_keeps_data_and_rejects_a_newer_schema(tmp_path):
    with LibraryDB.open(tmp_path) as db:
        db.upsert(Video(path="A - B - C.mp4"))
    with LibraryDB.open(tmp_path) as db:
        assert len(db.all()) == 1
        db.conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer vidlib"):
        LibraryDB.open(tmp_path)


# ── The database's old name ──────────────────────────────────────────


def _legacy_db(root):
    """A database as optimiselib left it: under its old name, with an unflushed WAL."""
    with LibraryDB.open(root) as db:
        db.upsert(Video(path="A - B - C.mp4"))
    (root / DB_NAME).rename(root / LEGACY_DB_NAME)
    (root / f"{LEGACY_DB_NAME}-wal").write_bytes(b"")


def test_an_optimiselib_database_is_renamed_on_first_open(tmp_path):
    _legacy_db(tmp_path)
    with LibraryDB.open(tmp_path) as db:
        assert [v.path for v in db.all()] == ["A - B - C.mp4"]
    assert not (tmp_path / LEGACY_DB_NAME).exists()
    assert not (tmp_path / f"{LEGACY_DB_NAME}-wal").exists()
    assert (tmp_path / DB_NAME).is_file()


def test_a_dry_run_reads_the_old_database_without_renaming_it(tmp_path):
    _legacy_db(tmp_path)
    with LibraryDB.open(tmp_path, in_memory=True) as db:
        assert len(db.all()) == 1
    assert (tmp_path / LEGACY_DB_NAME).is_file() and not (tmp_path / DB_NAME).exists()


def test_with_both_databases_the_old_one_is_left_alone(tmp_path):
    _legacy_db(tmp_path)
    with LibraryDB.open(tmp_path):                                # adopts it
        pass
    (tmp_path / LEGACY_DB_NAME).write_bytes(b"stale")
    assert legacy_db_conflict(tmp_path)
    with LibraryDB.open(tmp_path) as db:
        assert len(db.all()) == 1
    assert (tmp_path / LEGACY_DB_NAME).read_bytes() == b"stale"


# ── JSON log import ────────────────────────────────────────────────────


def test_import_json_log(tmp_path):
    (tmp_path / "Lakes").mkdir()
    (tmp_path / "Lakes" / "Alice - Day 1 - Lakes [1080p].mp4").write_bytes(b"x" * 10)
    (tmp_path / "Dave - Hike - Lakes.mp4").write_bytes(b"y" * 7)
    log = {
        "Alice - Day 1 - Lakes [1080p].mp4": {
            "status": "sorted", "resolution": "1080p", "source_height": 1440, "duration": 60.0,
            "bit_depth": 10, "has_sub": True, "backend": "amd", "original_size": 99,
            "compressed_size": 10, "trip": "Lakes", "dest": "Lakes\\Alice - Day 1 - Lakes [1080p].mp4",
            "ts": "2026-01-01T00:00:00",
        },
        "Dave - Hike - Lakes.mp4": {"status": "reconciled", "reconciled": True,
                                     "resolution": "720p"},
        "Bob - Late - Lakes.mp4": {"status": "review", "reason": "downgrade",
                                    "compared_with": "x.mp4"},
        "Broken.mp4": {"status": "failed", "failures": 3},
    }
    (tmp_path / JSON_LOG_NAME).write_text(json.dumps(log), encoding="utf-8")

    with LibraryDB.open(tmp_path) as db:
        assert import_json_log(tmp_path, db) == 4
        alice = db.get("Lakes/Alice - Day 1 - Lakes [1080p].mp4")   # dest, '/'-separated
        assert alice is not None
        assert (alice.source_height, alice.bit_depth, alice.has_sub, alice.size) == (1440, 10, True, 10)
        assert alice.mtime > 0                                       # read from the file
        dave = db.get("Dave - Hike - Lakes.mp4")
        assert dave is not None and dave.status == "compressed"      # marker retired
        bob = db.get("_review/Bob - Late - Lakes.mp4")
        assert bob is not None and bob.reason == "downgrade"
        broken = db.get("Broken.mp4")
        assert broken is not None and broken.failures == 3
        assert [e["action"] for e in db.events()] == ["migrated"]

        assert not (tmp_path / JSON_LOG_NAME).exists()
        assert (tmp_path / (JSON_LOG_NAME + ".migrated")).exists()
        assert import_json_log(tmp_path, db) == 0                    # only ever once


def test_import_without_a_log_is_a_no_op(tmp_path):
    with LibraryDB.open(tmp_path) as db:
        assert import_json_log(tmp_path, db) == 0
