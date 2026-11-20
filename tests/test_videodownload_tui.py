"""video-download's TUI, driven headlessly with Textual's pilot."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys

import pytest

from toolboxcli.videodownload.cli import library_names
from toolboxcli.videodownload.core import KnownNames
from toolboxcli.videodownload.tui import QUEUE_FILE, DownloadApp, QueueTable, WordBar

KNOWN = KnownNames(people={"alice", "bob"}, collections={"lakes"})


def run(coro):
    return asyncio.run(coro)


async def _type(pilot, widget_id: str, text: str) -> None:
    """Set an input and wait for what it sets off to settle: a title change goes Input.Changed
    → WordBar.Changed → the fields → their own Input.Changed, more hops than one pause()
    reliably covers on a slow runner."""
    pilot.app.query_one(f"#{widget_id}").value = text
    last, stable = None, 0
    for _ in range(200):
        await pilot.pause(0.01)
        now = (_fields(pilot.app), _preview(pilot.app))
        stable = stable + 1 if now == last else 0
        if stable >= 3:
            return
        last = now
    raise AssertionError("the form never settled")


def _fields(app) -> tuple[str, str, str]:
    return tuple(app.query_one(f"#{i}").value for i in ("people", "name-title", "collection"))


def _preview(app) -> str:
    return str(app.query_one("#preview").render())


# ── labelling (no downloads) ───────────────────────────────────────────


def test_pasting_a_title_labels_it_and_fills_the_fields(tmp_path):
    async def scenario():
        app = DownloadApp(tmp_path, known=KNOWN)
        async with app.run_test() as pilot:
            await _type(pilot, "title", "Alice & Bob hike – Day 1 (Lakes) | SomeSite")
            assert _fields(app) == ("Alice, Bob", "hike Day 1", "Lakes")
            assert "Alice, Bob - hike Day 1 - Lakes" in _preview(app)
            assert "⚠" not in _preview(app)
    run(scenario())


def test_keys_relabel_words(tmp_path):
    async def scenario():
        app = DownloadApp(tmp_path)                      # nothing known: all title
        async with app.run_test() as pilot:
            await _type(pilot, "title", "Carol Dee swims in Peaks")
            assert _fields(app) == ("", "Carol Dee swims in Peaks", "")
            assert "no people, collection" in _preview(app)
            app.query_one(WordBar).focus()
            # Select "Carol Dee": people, not title.
            await pilot.press("shift+right", "p", "t")
            # Cursor is on "Dee": two right to "in", skip it; then "Peaks": collection only.
            await pilot.press("right", "right", "x", "right", "c", "t")
            assert _fields(app) == ("Carol Dee", "swims", "Peaks")
            assert "Carol Dee - swims - Peaks" in _preview(app)
            # p again on a word already a person turns it off.
            await pilot.press("home", "p")
            assert _fields(app)[0] == "Dee"
    run(scenario())


def test_fields_can_be_edited_by_hand(tmp_path):
    async def scenario():
        app = DownloadApp(tmp_path, known=KNOWN)
        async with app.run_test() as pilot:
            await _type(pilot, "title", "Alice at the lakes (Lakes)")
            await _type(pilot, "name-title", "Morning swim")
            assert "Alice - Morning swim - Lakes" in _preview(app)
    run(scenario())


def test_adding_needs_a_url(tmp_path):
    async def scenario():
        app = DownloadApp(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+s")
            assert app.query_one(QueueTable).row_count == 0
    run(scenario())


def test_library_names_are_read_only_and_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_DOWNLOAD_LIBRARY", raising=False)
    assert library_names(None) == KnownNames()
    assert library_names(str(tmp_path)) == KnownNames()           # no database there
    from toolboxcli.optimiselib.db import LibraryDB, Video
    with LibraryDB.open(tmp_path) as db:
        db.upsert(Video(path="Lakes/Alice, Bob - Day 1 - Lakes.mp4"))
        db.upsert(Video(path="Odd name.mp4"))
    before = (tmp_path / ".optimiselib.db").stat().st_mtime_ns
    monkeypatch.setenv("VIDEO_DOWNLOAD_LIBRARY", str(tmp_path))
    assert library_names(None) == KnownNames({"alice", "bob"}, {"lakes"})
    assert (tmp_path / ".optimiselib.db").stat().st_mtime_ns == before
    with sqlite3.connect(tmp_path / ".optimiselib.db") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1


# ── downloading, with a fake yt-dlp ────────────────────────────────────

FAKE_YTDLP = """\
#!{python}
import pathlib, sys, time
url = sys.argv[-1]
if "fail" in url:
    print("ERROR: simulated failure", flush=True)
    sys.exit(1)
for pct in (20, 60, 100):
    print(f"[download]  {{pct}}.0% of 1.00MiB at 3.00MiB/s ETA 00:01", flush=True)
    time.sleep(2 if "slow" in url else 0.05)
template = sys.argv[sys.argv.index("-o") + 1]
pathlib.Path(template.replace("%(title)s", "Video Title").replace("%(ext)s", "mp4")
             .replace("%%", "%")).write_text(url)
"""

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake yt-dlp is a POSIX script")


@pytest.fixture
def work(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "yt-dlp"
    script.write_text(FAKE_YTDLP.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    folder = tmp_path / "work"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return folder


async def _until(pilot, condition, timeout: float = 10) -> None:
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("timed out waiting")


def _saved(folder):
    return json.loads((folder / QUEUE_FILE).read_text())["items"]


@posix_only
def test_a_queued_download_runs_and_is_saved(work):
    async def scenario():
        app = DownloadApp(work, known=KNOWN)
        async with app.run_test() as pilot:
            await _type(pilot, "url", "https://x/a")
            await _type(pilot, "title", "Alice & Bob hike – Day 1 (Lakes) | SomeSite")
            await pilot.press("ctrl+s")
            assert app.query_one("#url").value == ""            # form cleared for the next
            await _until(pilot, lambda: app.items and app.items[0].status == "done")
        assert (work / "Alice, Bob - hike Day 1 - Lakes.mp4").read_text() == "https://x/a"
        assert _saved(work)[0]["status"] == "done"
    run(scenario())


@posix_only
def test_nothing_labelled_uses_the_videos_own_title(work):
    async def scenario():
        app = DownloadApp(work)
        async with app.run_test() as pilot:
            await _type(pilot, "url", "https://x/b")
            await pilot.press("ctrl+s")
            await _until(pilot, lambda: app.items and app.items[0].status == "done")
        assert (work / "Video Title.mp4").exists()
    run(scenario())


@posix_only
def test_a_failure_can_be_retried_and_removed(work):
    async def scenario():
        app = DownloadApp(work)
        async with app.run_test() as pilot:
            await _type(pilot, "url", "https://x/fail")
            await pilot.press("ctrl+s")
            await _until(pilot, lambda: app.items and app.items[0].status == "failed")
            assert app.items[0].error == "ERROR: simulated failure"
            app.query_one(QueueTable).focus()
            await pilot.press("r")
            await _until(pilot, lambda: app.items[0].status == "failed")
            await pilot.press("d")
            await pilot.pause()
            assert app.items == [] and app.query_one(QueueTable).row_count == 0
        assert _saved(work) == []
    run(scenario())


@posix_only
def test_cancelling_a_running_download(work):
    async def scenario():
        app = DownloadApp(work)
        async with app.run_test() as pilot:
            await _type(pilot, "url", "https://x/slow")
            await pilot.press("ctrl+s")
            await _until(pilot, lambda: app.items and app.items[0].status == "downloading")
            app.query_one(QueueTable).focus()
            await pilot.press("d")
            await _until(pilot, lambda: app.items[0].status == "cancelled")
        assert not list(work.glob("*.mp4"))
    run(scenario())


@posix_only
def test_an_unfinished_queue_resumes_on_start(work):
    (work / QUEUE_FILE).write_text(json.dumps({"version": 1, "items": [
        {"url": "https://x/r", "name": "Alice - Resumed - Lakes", "status": "downloading"},
        {"url": "https://x/old", "name": "Old", "status": "done"},
    ]}))

    async def scenario():
        app = DownloadApp(work)
        async with app.run_test() as pilot:
            assert app.query_one(QueueTable).row_count == 2
            await _until(pilot, lambda: app.items[0].status == "done")
            assert app.items[1].status == "done"                # history isn't re-run
        assert (work / "Alice - Resumed - Lakes.mp4").exists()
        assert not (work / "Old.mp4").exists()
    run(scenario())


@posix_only
def test_quitting_with_downloads_running_asks_first_and_requeues_them(work):
    async def scenario():
        app = DownloadApp(work)
        async with app.run_test() as pilot:
            await _type(pilot, "url", "https://x/slow")
            await pilot.press("ctrl+s")
            await _until(pilot, lambda: app.items and app.items[0].status == "downloading")
            await pilot.press("ctrl+q")
            await pilot.pause()
            assert app.is_running                               # first press only warns
            await pilot.press("ctrl+q")
        saved = _saved(work)
        assert saved[0]["url"] == "https://x/slow"
        assert saved[0]["status"] in ("downloading", "queued")   # resumes next time
    run(scenario())
