"""End-to-end runs of real commands on throwaway folders.

vidlib runs against tiny videos generated with ffmpeg; anything needing a tool that
isn't installed is skipped. Its web page and downloads are tested in test_vidlib_web.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import needs

from toolboxcli.vidlib import cli as vidlib
from toolboxcli.vidlib.db import LibraryDB, Video

# ── vidlib ────────────────────────────────────────────────────────


def _db(root: Path) -> LibraryDB:
    return LibraryDB.open(root)


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and not p.name.startswith(".")}


@pytest.fixture
def library(tmp_path, make_video):
    """A library root with one sorted file, two unsorted ones and a parked one."""
    root = tmp_path / "lib"
    make_video(root / "Lakes" / "Alice - Day 1 - Lakes.mp4", "1920x1080")
    make_video(root / "Dave - Hike - Lakes.mp4", "1280x720")
    make_video(root / "Eve - Untitled.mp4", "640x480")
    make_video(root / "_review" / "Parked - A - B.mp4", "640x480")
    return root


@needs("ffmpeg", "ffprobe")
def test_dry_run_plans_but_changes_nothing(library, run_cli):
    (library / ".optimiselib.json").write_text("{}")
    before = _files(library)
    result = run_cli("vidlib", [str(library), "-n", "-O", "-S"])
    assert result.returncode == 0, result.stderr
    assert "Dave - Hike - Lakes [720p].mp4" in result.stdout
    assert _files(library) == before
    assert not (library / ".vidlib.db").exists()       # worked on an in-memory copy
    assert (library / ".optimiselib.json").exists()          # old log not migrated away


@needs("ffmpeg", "ffprobe")
def test_dry_run_finds_a_duplicate_with_reordered_people_in_another_folder(tmp_path, make_video,
                                                                           run_cli):
    root = tmp_path / "lib"
    make_video(root / "Staging" / "Alice, Bob - Day 1 - Lakes [1080p].mp4", "1920x1080")
    make_video(root / "bob and ALICE - day 1 - lakes.mp4", "1280x720")
    result = run_cli("vidlib", [str(root), "-n", "-O", "-S"])
    assert result.returncode == 0, result.stderr
    assert "downgrade of Staging/Alice, Bob - Day 1 - Lakes [1080p].mp4" in result.stdout


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_old_log_migrates_and_reconciled_files_sort_without_encoding(library, run_cli):
    # Entries as the since-removed -R/--reconcile wrote them, in the old JSON log.
    (library / ".optimiselib.json").write_text(json.dumps({
        "Alice - Day 1 - Lakes.mp4": {"status": "sorted", "reconciled": True,
                                      "dest": "Lakes/Alice - Day 1 - Lakes.mp4"},
        "Dave - Hike - Lakes.mp4": {"status": "reconciled", "reconciled": True},
        "Eve - Untitled.mp4": {"status": "reconciled", "reconciled": True},
    }))
    result = run_cli("vidlib", [str(library), "-O", "-S", "-g", "cpu"])
    assert result.returncode == 0, result.stderr
    assert "Imported 3 entries" in result.stdout
    assert (library / ".optimiselib.json.migrated").exists()
    assert _files(library) == {
        "Lakes/Alice - Day 1 - Lakes.mp4",
        "Lakes/Dave - Hike - Lakes [720p].mp4",
        "Eve - Untitled [480p].mp4",            # no collection field: stays in root
        "_review/Parked - A - B.mp4",
    }
    with _db(library) as db:
        dave = db.get("Lakes/Dave - Hike - Lakes [720p].mp4")
        assert dave is not None and dave.status == "sorted" and dave.backend == ""  # not encoded
        assert db.get("Dave - Hike - Lakes.mp4") is None
        eve = db.get("Eve - Untitled [480p].mp4")
        assert eve is not None and eve.status == "awaiting_trip"
        parked = db.get("_review/Parked - A - B.mp4")         # found by the library sync
        assert parked is not None and parked.status == "review"


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_new_file_is_encoded_tagged_and_sorted(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    make_video(root / "Lakes" / "Alice - Day 1 - Lakes.mp4", "1920x1080")
    make_video(root / "Bob - Swim - Lakes.mkv", "1280x720", seconds=3)
    result = run_cli("vidlib", [str(root), "-O", "-S", "-g", "cpu"])
    assert result.returncode == 0, result.stderr + result.stdout
    files = _files(root)
    # Kept as a re-encoded .mp4 when that came out smaller, else the original retagged.
    done = files & {"Lakes/Bob - Swim - Lakes [720p].mp4", "Lakes/Bob - Swim - Lakes [720p].mkv"}
    assert len(done) == 1
    with _db(root) as db:
        bob = db.get(done.pop())
        assert bob is not None and bob.status == "sorted" and bob.backend == "cpu"
        assert bob.size > 0 and bob.mtime > 0
        assert db.get("Bob - Swim - Lakes.mkv") is None
    assert not any(name.endswith((".tmp", ".processing")) for name in files)


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_a_file_renamed_by_hand_is_sorted_not_re_encoded(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    make_video(root / "Eve - Untitled.mp4", "640x480")
    assert run_cli("vidlib", [str(root), "-O", "-S", "-g", "cpu"]).returncode == 0
    waiting = next(p for p in root.iterdir() if p.name.startswith("Eve - Untitled ["))
    # Add the missing collection field by hand.
    waiting.rename(root / waiting.name.replace("Eve - Untitled", "Eve - Untitled - Lakes"))

    result = run_cli("vidlib", [str(root), "-O", "-S", "-g", "cpu"])
    assert result.returncode == 0, result.stderr
    assert "already optimised" in result.stdout
    sorted_file = next(iter(_files(root)))
    assert sorted_file.startswith("Lakes/Eve - Untitled - Lakes [480p]")
    with _db(root) as db:
        actions = [e["action"] for e in db.events()]
        assert "renamed" in actions and actions[-1] == "sorted"


@needs("ffmpeg", "ffprobe")
def test_adopt_tags_and_files_without_encoding(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    root.mkdir()
    src = make_video(tmp_path / "elsewhere" / "Bob - Swim - Lakes.mkv", "1280x720")
    size = src.stat().st_size
    # No HandBrakeCLI needed (nor checked for): nothing is encoded.
    result = run_cli("vidlib", [str(root), "-A", str(src)])
    assert result.returncode == 0, result.stderr + result.stdout
    assert not src.exists()
    filed = root / "Lakes" / "Bob - Swim - Lakes [720p].mkv"
    assert _files(root) == {"Lakes/Bob - Swim - Lakes [720p].mkv"}
    assert filed.stat().st_size == size            # the same file, not an encode
    with _db(root) as db:
        row = db.get("Lakes/Bob - Swim - Lakes [720p].mkv")
        assert row is not None and row.status == "sorted" and row.backend == ""
        actions = [e["action"] for e in db.events()]
        assert actions[:1] == ["adopted"] and actions[-1] == "sorted"


@needs("ffmpeg", "ffprobe")
def test_adopt_beside_a_running_vidlib_leaves_the_filing_to_it(tmp_path, make_video, run_cli):
    from toolboxcli.vidlib.instance import LibraryLock
    root = tmp_path / "lib"
    make_video(root / "Lakes" / "Alice - Day 1 - Lakes.mp4", "1920x1080")
    src = make_video(tmp_path / "Bob - Swim - Lakes.mp4", "1280x720")
    with LibraryLock(root):                         # "another vidlib" holds the library
        result = run_cli("vidlib", [str(root), "-A", str(src)])
    assert result.returncode == 0, result.stderr + result.stdout
    assert "next poll" in result.stdout
    assert (root / "Bob - Swim - Lakes.mp4").is_file()
    with _db(root) as db:
        row = db.get("Bob - Swim - Lakes.mp4")
        assert row is not None and row.status == "compressed"
        assert row.size == (root / "Bob - Swim - Lakes.mp4").stat().st_size
        # Which is what the pipeline reads as "sort only".
        assert vidlib.already_done(row, 8, "auto")


@needs("ffmpeg", "ffprobe")
def test_adopt_keeps_a_known_files_row_and_never_overwrites(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    known = make_video(root / "Stash" / "Bob - Swim - Lakes.mp4", "1280x720")
    with _db(root) as db:
        vidlib.sync_library(root, db)               # recorded from its folder, probed
    clash = make_video(tmp_path / "Eve - Run - Hills.mp4", "640x480")
    make_video(root / "Eve - Run - Hills.mp4", "640x480")
    from toolboxcli.vidlib.instance import LibraryLock
    with LibraryLock(root):
        result = run_cli("vidlib", [str(root), "-A", str(known), str(clash)])
    assert result.returncode == 0, result.stderr + result.stdout
    assert "already has a Eve - Run - Hills.mp4" in result.stderr + result.stdout
    assert clash.exists()                           # refused, left where it was
    with _db(root) as db:
        assert db.get("Stash/Bob - Swim - Lakes.mp4") is None
        row = db.get("Bob - Swim - Lakes.mp4")
        # The same row, moved: what the sync probed is still there.
        assert row is not None and row.status == "compressed" and row.height == 720


def test_adopt_refuses_force_and_terminal_modes(tmp_path, run_cli):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"")
    for extra in (["-F"], ["-l"], ["-r"]):
        result = run_cli("vidlib", [str(tmp_path), "-A", str(video), *extra])
        assert result.returncode != 0 and "-A/--adopt" in result.stderr, extra


def test_sync_follows_moves_records_folders_and_marks_missing(tmp_path):
    root = tmp_path
    (root / "Lakes").mkdir()
    staged = root / "Alice - Day 1 - Lakes [720p].mp4"
    staged.write_bytes(b"a" * 10)
    gone = root / "Lakes" / "Bob - Gone - Lakes.mp4"
    with _db(root) as db:
        vidlib.record(db, root, staged, "awaiting_trip", height=720)
        db.upsert(Video(path=vidlib.rel_path(root, gone), size=3, mtime=1.0))

        moved = root / "Lakes" / staged.name
        staged.rename(moved)                                         # a hand move
        (root / "Lakes" / "Carol - New - Lakes.mp4").write_bytes(b"c")   # added by hand
        (root / "Dan - Fresh - Lakes.mp4").write_bytes(b"d")             # new root input
        vidlib.sync_library(root, db)

        follow = db.get("Lakes/Alice - Day 1 - Lakes [720p].mp4")
        assert follow is not None and follow.height == 720            # row followed the file
        assert db.get(staged.name) is None
        carol = db.get("Lakes/Carol - New - Lakes.mp4")
        assert carol is not None and carol.status == "sorted"
        assert db.get("Dan - Fresh - Lakes.mp4") is None               # left for the pipeline
        bob = db.get("Lakes/Bob - Gone - Lakes.mp4")
        assert bob is not None and bob.status == "missing"

        gone.write_bytes(b"b" * 3)                                   # it comes back
        vidlib.sync_library(root, db)
        bob = db.get("Lakes/Bob - Gone - Lakes.mp4")
        assert bob is not None and bob.status == "sorted"


def test_sync_refreshes_mtimes_so_renames_still_match_after_a_disk_move(tmp_path):
    root = tmp_path
    (root / "Lakes").mkdir()
    video = root / "Lakes" / "Alice - Day 1 - Lakes [720p].mp4"
    video.write_bytes(b"a" * 10)
    os.utime(video, (1_700_000_000.37, 1_700_000_000.37))         # exFAT keeps 10 ms
    with _db(root) as db:
        vidlib.sync_library(root, db)
        before = db.get(vidlib.rel_path(root, video))
        assert before is not None and before.status == "sorted"

        os.utime(video, (1_700_000_000, 1_700_000_000))            # copied to HFS+: whole s
        vidlib.sync_library(root, db)
        row = db.get(vidlib.rel_path(root, video))
        assert row is not None and row.mtime == 1_700_000_000

        renamed = video.with_name("Alice - Day One - Lakes [720p].mp4")
        video.rename(renamed)                                      # a later hand rename
        vidlib.sync_library(root, db)
        assert db.get(vidlib.rel_path(root, video)) is None
        follow = db.get(vidlib.rel_path(root, renamed))
        assert follow is not None and follow.status == "sorted"
        actions = [e["action"] for e in db.events()]
        assert "renamed" in actions and "missing" not in actions



def test_downloads_in_incoming_are_never_recorded(tmp_path):
    root = tmp_path
    (root / "_incoming").mkdir()
    (root / "_incoming" / "Alice - Half - Lakes.mp4").write_bytes(b"partial")
    (root / "Lakes").mkdir()
    (root / "Lakes" / "_incoming").mkdir()                 # only the root's is special
    (root / "Lakes" / "_incoming" / "Bob - Kept - Lakes.mp4").write_bytes(b"b")
    with _db(root) as db:
        vidlib.sync_library(root, db)
        assert [v.path for v in db.all()] == ["Lakes/_incoming/Bob - Kept - Lakes.mp4"]
    assert vidlib.all_videos(root) == [root / "Lakes" / "_incoming" / "Bob - Kept - Lakes.mp4"]


def test_a_folder_marked_vidlib_ignore_is_not_part_of_the_library(tmp_path):
    root = tmp_path
    (root / "Lakes").mkdir()
    (root / "Not mine" / "deep").mkdir(parents=True)
    (root / "Not mine" / ".vidlib-ignore").write_text("")
    (root / "Not mine" / "deep" / "Alice - Theirs - Lakes.mp4").write_bytes(b"t")
    kept = root / "Lakes" / "Alice - Day 1 - Lakes.mp4"
    kept.write_bytes(b"k")
    moving = root / "Lakes" / "Bob - Old - Lakes.mp4"
    moving.write_bytes(b"m" * 5)
    with _db(root) as db:
        vidlib.sync_library(root, db)
        assert sorted(v.path for v in db.all()) == [
            "Lakes/Alice - Day 1 - Lakes.mp4", "Lakes/Bob - Old - Lakes.mp4"]   # not recorded

        # A recorded file moved into the ignored folder is forgotten, not "missing"…
        moving.rename(root / "Not mine" / moving.name)
        # …and so is a row whose whole folder gets marked later.
        (root / "Lakes" / ".vidlib-ignore").write_text("")
        vidlib.sync_library(root, db)
        assert db.all(include_missing=True) == []
        assert {e["action"] for e in db.events()} >= {"ignored"}
    assert vidlib.all_videos(root) == []
    assert vidlib.find_folder(root, "lakes") is None          # never chosen to file into


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_nothing_is_filed_into_an_ignored_collection_folder(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    (root / "Clips").mkdir(parents=True)
    (root / "Clips" / ".vidlib-ignore").write_text("")
    make_video(root / "Eve - Short - Clips.mp4", "640x480")
    result = run_cli("vidlib", [str(root), "-O", "-S", "-g", "cpu"])
    assert result.returncode == 0, result.stderr
    assert "marked .vidlib-ignore" in result.stdout
    assert sorted(p.name for p in (root / "Clips").iterdir()) == [".vidlib-ignore"]
    [left] = [p for p in root.glob("*.mp4")]
    assert left.name.startswith("Eve - Short - Clips")


@pytest.fixture
def catalogue(tmp_path, make_video):
    """A sorted library to query: a reordered-people duplicate pair, and a tagged file."""
    root = tmp_path / "lib"
    make_video(root / "Lakes" / "Alice, Bob - Day 1 - Lakes [1080p].mp4", "1920x1080")
    make_video(root / "Lakes" / "Bob and Alice - Day 1 - Lakes [720p].mp4", "1280x720")
    make_video(root / "Peaks" / "Carol - Summit - Peaks [720p Restored].mp4", "1280x720")
    make_video(root / "_review" / "Alice - Parked - Lakes.mp4", "640x480")
    return root


def _listed(stdout: str) -> set[str]:
    names = ("Alice, Bob - Day 1", "Bob and Alice - Day 1", "Carol - Summit", "Alice - Parked")
    return {n for n in names if n in stdout}


@needs("ffmpeg", "ffprobe")
@pytest.mark.parametrize("filters, expected", [
    ([], {"Alice, Bob - Day 1", "Bob and Alice - Day 1", "Carol - Summit"}),  # not _review/
    (["-a", "ALICE"], {"Alice, Bob - Day 1", "Bob and Alice - Day 1"}),
    (["-a", "car"], {"Carol - Summit"}),
    (["-R", "720"], {"Bob and Alice - Day 1", "Carol - Summit"}),
    (["-c", "peaks", "-T", "restored"], {"Carol - Summit"}),
    (["-a", "alice", "-R", "1080p"], {"Alice, Bob - Day 1"}),
    (["-a", "nobody"], set()),
    (["-U"], set()),                                   # every catalogue name is complete
])
def test_list_filters(catalogue, run_cli, filters, expected):
    # Filters on their own imply -l.
    result = run_cli("vidlib", [str(catalogue), *filters] if filters else [str(catalogue), "-l"])
    assert result.returncode == 0, result.stderr
    assert _listed(result.stdout) == expected
    assert f"{len(expected)} file" in result.stdout


@needs("ffmpeg", "ffprobe")
def test_unnamed_files_are_listed_and_counted(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    make_video(root / "Lakes" / "Alice - Day 1 - Lakes.mp4", "640x480")
    make_video(root / "Lakes" / "Movie - Directors Cut.mp4", "640x480")
    make_video(root / "Misc" / "IMG_1234.mp4", "640x480")
    listed = run_cli("vidlib", [str(root), "-U"])
    assert listed.returncode == 0, listed.stderr
    assert "Movie - Directors Cut.mp4" in listed.stdout and "IMG_1234.mp4" in listed.stdout
    assert "Alice - Day 1" not in listed.stdout
    assert "2 files matching not named People - Title - Collection" in listed.stdout
    report = run_cli("vidlib", [str(root), "-r"])
    assert "2 file(s) aren't named People - Title - Collection" in report.stdout


@needs("ffmpeg", "ffprobe")
def test_list_to_a_file_writes_plain_names(catalogue, run_cli, tmp_path):
    out = tmp_path / "list.txt"
    result = run_cli("vidlib", [str(catalogue), "-l", str(out), "-R", "720p"])
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8").splitlines() == [
        "Bob and Alice - Day 1 - Lakes [720p].mp4",
        "Carol - Summit - Peaks [720p Restored].mp4",
    ]


@needs("ffmpeg", "ffprobe")
def test_duplicates_report_groups_reordered_people(catalogue, run_cli):
    before = _files(catalogue)
    result = run_cli("vidlib", [str(catalogue), "-D"])
    assert result.returncode == 0, result.stderr
    assert _listed(result.stdout) == {"Alice, Bob - Day 1", "Bob and Alice - Day 1"}
    assert "1 video(s) held more than once (2 files)" in result.stdout
    assert _files(catalogue) == before                       # a report only


@needs("ffmpeg", "ffprobe")
def test_duplicates_report_when_there_are_none(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    make_video(root / "Lakes" / "Alice - Day 1 - Lakes.mp4", "640x480")
    result = run_cli("vidlib", [str(root), "-D"])
    assert result.returncode == 0, result.stderr
    assert "No duplicates" in result.stdout


def test_recover_interrupted_restores_sources_and_drops_temp_output(tmp_path):
    (tmp_path / "Clip.mkv.processing").write_bytes(b"source")
    (tmp_path / "Clip [720p].mp4.tmp").write_bytes(b"partial")
    assert vidlib.recover_interrupted(tmp_path) == ["Clip.mkv"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Clip.mkv"]


def test_unique_path_never_overwrites(tmp_path):
    taken = tmp_path / "A.mp4"
    taken.write_bytes(b"")
    assert vidlib.unique_path(tmp_path / "B.mp4") == tmp_path / "B.mp4"
    assert vidlib.unique_path(taken) == tmp_path / "A (2).mp4"


@pytest.mark.parametrize("fields, expected", [
    (None, True),
    ({"status": "sorted"}, True),
    ({"status": "failed", "failures": 2}, True),
    ({"status": "failed", "failures": 3}, False),
    ({"status": "skipped", "reason": "destination exists"}, False),
    ({"status": "awaiting_trip"}, False),
])
def test_should_process(tmp_path, fields, expected):
    video = tmp_path / "No Trip [720p].mp4"
    with _db(tmp_path) as db:
        if fields:
            db.upsert(Video(path=video.name, **fields))
        assert vidlib.should_process(video, tmp_path, db) is expected


@pytest.mark.parametrize("status, pref, depth, done", [
    (None, "auto", 8, False),
    ("sorted", "auto", 10, True),
    ("compressed", "auto", 8, True),
    ("failed", "auto", 8, False),
    ("missing", "auto", 8, False),
    ("sorted", "8", 10, False),      # forced 8-bit re-encodes a 10-bit file
    ("sorted", "10", 8, False),
    ("sorted", "10", 10, True),
])
def test_already_done(status, pref, depth, done):
    row = Video(path="x.mp4", status=status) if status else None
    assert vidlib.already_done(row, depth, pref) is done


# ── vidlib's web page ──────────────────────────────────────────────────

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake binaries are POSIX scripts")


@pytest.fixture
def fake_downloader(tmp_path):
    """A bin dir with stand-ins for yt-dlp and aria2c, so -X gets past require_tool."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("yt-dlp", "aria2c"):
        script = bin_dir / name
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
    return bin_dir


@pytest.mark.parametrize("extra", [["-W"], ["-n"], ["-O"], ["-l"], ["-a", "x"], ["-D"]])
def test_download_only_refuses_the_terminal_only_modes(tmp_path, run_cli, extra):
    result = run_cli("vidlib", [str(tmp_path), "-X", str(tmp_path), *extra])
    assert result.returncode == 1
    assert "can't be combined" in result.stderr


@posix_only
def test_download_only_needs_another_folder(tmp_path, run_cli, fake_downloader):
    import os
    path = f"{fake_downloader}{os.pathsep}{os.environ['PATH']}"
    result = run_cli("vidlib", [str(tmp_path), "-X", str(tmp_path), "-b"],
                     env_extra={"PATH": path})
    assert result.returncode == 1
    assert "other than the library root" in result.stderr


@posix_only
def test_a_busy_port_moves_the_page_to_the_next_one(tmp_path, sandbox_env, fake_downloader):
    import os
    import queue
    import socket
    import subprocess
    import threading
    import time
    env = dict(sandbox_env, PATH=f"{fake_downloader}{os.pathsep}{os.environ['PATH']}")
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        dest = tmp_path / "dl"
        dest.mkdir()
        proc = subprocess.Popen(
            [sys.executable, "-m", "toolboxcli.vidlib.cli", str(tmp_path), "-X", str(dest),
             "-b", "-P", str(port)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        assert proc.stdout is not None
        lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout],
                         daemon=True).start()
        out = ""
        try:
            deadline = time.monotonic() + 60
            while "Web page at" not in out and time.monotonic() < deadline:
                try:
                    out += lines.get(timeout=1)
                except queue.Empty:
                    if proc.poll() is not None:
                        break
        finally:
            proc.terminate()
            proc.wait(timeout=30)
    assert f"Port {port} is in use" in out, out
    assert "Web page at http://127.0.0.1:" in out and f":{port}/" not in out, out

