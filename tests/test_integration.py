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

# ── optimiselib ────────────────────────────────────────────────────────


def _log(root: Path) -> dict:
    return json.loads((root / ".optimiselib.json").read_text())


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
    before = _files(library)
    result = run_cli("optimiselib", [str(library), "-n", "-O", "-S"])
    assert result.returncode == 0, result.stderr
    assert "Dave - Hike - Lakes [720p].mp4" in result.stdout
    assert _files(library) == before


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_reconciled_files_are_sorted_without_encoding(library, run_cli):
    # Log entries as the since-removed -R/--reconcile wrote them: still in real libraries,
    # and still meaning "already compressed, don't encode".
    (library / ".optimiselib.json").write_text(json.dumps({
        "Alice - Day 1 - Lakes.mp4": {"status": "sorted", "reconciled": True},
        "Dave - Hike - Lakes.mp4": {"status": "reconciled", "reconciled": True},
        "Eve - Untitled.mp4": {"status": "reconciled", "reconciled": True},
    }))
    result = run_cli("optimiselib", [str(library), "-O", "-S", "-g", "cpu"])
    assert result.returncode == 0, result.stderr
    assert _files(library) == {
        "Lakes/Alice - Day 1 - Lakes.mp4",
        "Lakes/Dave - Hike - Lakes [720p].mp4",
        "Eve - Untitled [480p].mp4",            # no collection field: stays in root
        "_review/Parked - A - B.mp4",
    }
    log = _log(library)
    assert "Dave - Hike - Lakes.mp4" not in log   # entry moved to the tagged name
    dave = log["Dave - Hike - Lakes [720p].mp4"]
    assert dave["reconciled"] and "backend" not in dave
    assert log["Eve - Untitled [480p].mp4"]["status"] == "awaiting_trip"


@needs("ffmpeg", "ffprobe", "HandBrakeCLI")
def test_new_file_is_encoded_tagged_and_sorted(tmp_path, make_video, run_cli):
    root = tmp_path / "lib"
    make_video(root / "Lakes" / "Alice - Day 1 - Lakes.mp4", "1920x1080")
    make_video(root / "Bob - Swim - Lakes.mkv", "1280x720", seconds=3)
    result = run_cli("optimiselib", [str(root), "-O", "-S", "-g", "cpu"])
    assert result.returncode == 0, result.stderr + result.stdout
    files = _files(root)
    # Kept as a re-encoded .mp4 when that came out smaller, else the original retagged.
    assert files & {"Lakes/Bob - Swim - Lakes [720p].mp4", "Lakes/Bob - Swim - Lakes [720p].mkv"}
    entry = next(v for k, v in _log(root).items() if k.startswith("Bob - Swim - Lakes [720p]"))
    assert entry["status"] == "sorted" and entry["backend"] == "cpu"
    assert not any(name.endswith((".tmp", ".processing")) for name in files)


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


@pytest.mark.parametrize("entry, expected", [
    (None, True),
    ({"status": "sorted"}, True),
    ({"status": "failed", "failures": 2}, True),
    ({"status": "failed", "failures": 3}, False),
    ({"status": "skipped", "reason": "destination exists"}, False),
    ({"status": "awaiting_trip"}, False),
])
def test_should_process(entry, expected):
    video = Path("No Trip [720p].mp4")
    log = {video.name: entry} if entry else {}
    assert optimiselib.should_process(video, log) is expected


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
