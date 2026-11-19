"""End-to-end runs of real commands on throwaway folders.

optimiselib runs against tiny videos generated with ffmpeg; anything needing a tool that
isn't installed is skipped. video-download runs against a fake yt-dlp that just writes a
file, so its urls.txt bookkeeping can be exercised without the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import needs

from toolboxcli.optimiselib import cli as optimiselib
from toolboxcli.optimiselib.db import LibraryDB, Video

# ── optimiselib ────────────────────────────────────────────────────────


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
    result = run_cli("optimiselib", [str(library), "-n", "-O", "-S"])
    assert result.returncode == 0, result.stderr
    assert "Dave - Hike - Lakes [720p].mp4" in result.stdout
    assert _files(library) == before
    assert not (library / ".optimiselib.db").exists()       # worked on an in-memory copy
    assert (library / ".optimiselib.json").exists()          # old log not migrated away


@needs("ffmpeg", "ffprobe")
def test_dry_run_finds_a_duplicate_with_reordered_people_in_another_folder(tmp_path, make_video,
                                                                           run_cli):
    root = tmp_path / "lib"
    make_video(root / "Staging" / "Alice, Bob - Day 1 - Lakes [1080p].mp4", "1920x1080")
    make_video(root / "bob and ALICE - day 1 - lakes.mp4", "1280x720")
    result = run_cli("optimiselib", [str(root), "-n", "-O", "-S"])
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
    result = run_cli("optimiselib", [str(library), "-O", "-S", "-g", "cpu"])
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
    result = run_cli("optimiselib", [str(root), "-O", "-S", "-g", "cpu"])
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
    assert run_cli("optimiselib", [str(root), "-O", "-S", "-g", "cpu"]).returncode == 0
    waiting = next(p for p in root.iterdir() if p.name.startswith("Eve - Untitled ["))
    # Add the missing collection field by hand.
    waiting.rename(root / waiting.name.replace("Eve - Untitled", "Eve - Untitled - Lakes"))

    result = run_cli("optimiselib", [str(root), "-O", "-S", "-g", "cpu"])
    assert result.returncode == 0, result.stderr
    assert "already optimised" in result.stdout
    sorted_file = next(iter(_files(root)))
    assert sorted_file.startswith("Lakes/Eve - Untitled - Lakes [480p]")
    with _db(root) as db:
        actions = [e["action"] for e in db.events()]
        assert "renamed" in actions and actions[-1] == "sorted"


def test_sync_follows_moves_records_folders_and_marks_missing(tmp_path):
    root = tmp_path
    (root / "Lakes").mkdir()
    staged = root / "Alice - Day 1 - Lakes [720p].mp4"
    staged.write_bytes(b"a" * 10)
    gone = root / "Lakes" / "Bob - Gone - Lakes.mp4"
    with _db(root) as db:
        optimiselib.record(db, root, staged, "awaiting_trip", height=720)
        db.upsert(Video(path=optimiselib.rel_path(root, gone), size=3, mtime=1.0))

        moved = root / "Lakes" / staged.name
        staged.rename(moved)                                         # a hand move
        (root / "Lakes" / "Carol - New - Lakes.mp4").write_bytes(b"c")   # added by hand
        (root / "Dan - Fresh - Lakes.mp4").write_bytes(b"d")             # new root input
        optimiselib.sync_library(root, db)

        follow = db.get("Lakes/Alice - Day 1 - Lakes [720p].mp4")
        assert follow is not None and follow.height == 720            # row followed the file
        assert db.get(staged.name) is None
        carol = db.get("Lakes/Carol - New - Lakes.mp4")
        assert carol is not None and carol.status == "sorted"
        assert db.get("Dan - Fresh - Lakes.mp4") is None               # left for the pipeline
        bob = db.get("Lakes/Bob - Gone - Lakes.mp4")
        assert bob is not None and bob.status == "missing"

        gone.write_bytes(b"b" * 3)                                   # it comes back
        optimiselib.sync_library(root, db)
        bob = db.get("Lakes/Bob - Gone - Lakes.mp4")
        assert bob is not None and bob.status == "sorted"


def test_recover_interrupted_restores_sources_and_drops_temp_output(tmp_path):
    (tmp_path / "Clip.mkv.processing").write_bytes(b"source")
    (tmp_path / "Clip [720p].mp4.tmp").write_bytes(b"partial")
    assert optimiselib.recover_interrupted(tmp_path) == ["Clip.mkv"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Clip.mkv"]


def test_unique_path_never_overwrites(tmp_path):
    taken = tmp_path / "A.mp4"
    taken.write_bytes(b"")
    assert optimiselib.unique_path(tmp_path / "B.mp4") == tmp_path / "B.mp4"
    assert optimiselib.unique_path(taken) == tmp_path / "A (2).mp4"


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
        assert optimiselib.should_process(video, tmp_path, db) is expected


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
    assert optimiselib.already_done(row, depth, pref) is done


# ── video-download ─────────────────────────────────────────────────────

FAKE_YTDLP = """\
#!{python}
import pathlib, sys
args = sys.argv[1:]
url, template = args[-1], args[args.index("-o") + 1]
if "fail" in url:
    print("ERROR: simulated failure for " + url)
    sys.exit(1)
if "add" in url:  # simulates the user adding a line to urls.txt mid-run
    with open("urls.txt", "a") as f:
        f.write("https://example.com/late Late one\\n")
name = template.replace("%(title)s", "Video Title").replace("%(ext)s", "mp4").replace("%%", "%")
pathlib.Path(name).write_text(url)
print("[download] Destination: " + name)
"""


@pytest.fixture
def fake_downloader(tmp_path):
    """A bin dir holding a fake yt-dlp (and an aria2c it never calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("yt-dlp", FAKE_YTDLP.format(python=sys.executable)),
                       ("aria2c", "#!/bin/sh\nexit 0\n")):
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)
    return bin_dir


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake binaries are POSIX scripts")


def _run_download(run_cli, work: Path, bin_dir: Path, *args: str):
    import os
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return run_cli("video-download", list(args), cwd=work, env_extra={"PATH": path})


@posix_only
@pytest.mark.parametrize("jobs", ["1", "3"])
def test_successes_leave_urls_txt_and_failures_stay(tmp_path, fake_downloader, run_cli, jobs):
    work = tmp_path / "work"
    work.mkdir()
    (work / "urls.txt").write_text(
        "# my list\n"
        "https://example.com/a Trip - Day 1\n"
        "https://example.com/fail Broken\n"
        "\n"
        "https://example.com/b\n"
        "https://example.com/c 100% Done\n",
        encoding="utf-8",
    )
    result = _run_download(run_cli, work, fake_downloader, "-j", jobs)
    assert result.returncode == 1, result.stdout
    assert (work / "urls.txt").read_text() == "# my list\nhttps://example.com/fail Broken\n\n"
    assert sorted(p.name for p in work.iterdir()) == [
        "100% Done.mp4", "Trip - Day 1.mp4", "Video Title.mp4", "urls.txt"]
    assert "3 succeeded" in result.stdout and "1 failed" in result.stdout


@posix_only
def test_lines_added_mid_run_are_downloaded_with_yes(tmp_path, fake_downloader, run_cli):
    work = tmp_path / "work"
    work.mkdir()
    (work / "urls.txt").write_text("https://example.com/add First\n", encoding="utf-8")
    result = _run_download(run_cli, work, fake_downloader, "-y")
    assert result.returncode == 0, result.stdout
    assert "1 new line(s) were added" in result.stdout
    assert (work / "urls.txt").read_text() == ""
    assert sorted(p.name for p in work.iterdir()) == ["First.mp4", "Late one.mp4", "urls.txt"]


@posix_only
def test_lines_added_mid_run_are_left_when_nobody_can_answer(tmp_path, fake_downloader, run_cli):
    # stdin closed (cron, nohup): the prompt must not crash, and silence means "no".
    work = tmp_path / "work"
    work.mkdir()
    (work / "urls.txt").write_text("https://example.com/add First\n", encoding="utf-8")
    result = _run_download(run_cli, work, fake_downloader)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "taking that as no" in result.stdout
    assert "All done: 1 succeeded" in result.stdout
    assert (work / "urls.txt").read_text() == "https://example.com/late Late one\n"


def test_missing_urls_txt_is_an_error(tmp_path, run_cli):
    result = run_cli("video-download", [], cwd=tmp_path)
    assert result.returncode == 1
    assert "no urls.txt found" in result.stderr
